import stat
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from voice_agent import audio, cli, tts
from voice_agent.agent import Agent, response_profile
from voice_agent.audit import AuditLog
from voice_agent.stt import _clean
from voice_agent.tools import build_registry, personal, system
from voice_agent.tools.base import Tool, ToolContext
from voice_agent.tools.browser import _allowed_url
from voice_agent.tools.files import (
    ToolError,
    _list_files,
    _read_file,
    _search_files,
    _validate_check,
)
from voice_agent.tools.system import _search_result_url, _SearchParser
from voice_agent.util import atomic_write, redact


class SecurityGuardTests(unittest.TestCase):
    def test_redact_common_credentials(self) -> None:
        text = "token=super-secret-value api_key='another-secret-value'"
        cleaned = redact(text)
        self.assertNotIn("super-secret-value", cleaned)
        self.assertNotIn("another-secret-value", cleaned)
        self.assertEqual(cleaned.count("[REDACTED]"), 2)

    def test_redact_private_key(self) -> None:
        text = "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----"
        self.assertEqual(redact(text), "[REDACTED]")

    def test_check_rejects_shell_syntax(self) -> None:
        with self.assertRaises(ToolError):
            _validate_check("python -m compileall src; curl example.test")

    def test_check_allows_read_only_compile(self) -> None:
        self.assertEqual(_validate_check("python -m compileall -q src"), ["python", "-m", "compileall", "-q", "src"])

    def test_wake_lock_records_current_process(self) -> None:
        with TemporaryDirectory() as directory:
            lock_path = Path(directory) / "wake.lock"
            with patch.object(cli, "WAKE_LOCK_PATH", lock_path):
                cli._create_wake_lock()
            self.assertEqual(lock_path.read_text(encoding="ascii"), str(cli.os.getpid()))

    def test_doctor_handles_missing_optional_gpu_tool(self) -> None:
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text("{}", encoding="utf-8")
            with patch.object(cli, "CONFIG_PATH", config_path), patch.object(
                cli, "load_config", return_value={"agent": {}}
            ), patch.object(cli.stt, "whisper_binary", return_value=None), patch.object(
                cli.stt, "whisper_model", return_value=Path("/missing/model")
            ), patch.object(cli.llm, "version", side_effect=RuntimeError("offline")), patch.object(
                cli, "run", side_effect=FileNotFoundError("nvidia-smi")
            ):
                self.assertEqual(cli._doctor(SimpleNamespace()), 1)

    def test_llm_default_generation_limit_is_bounded(self) -> None:
        from voice_agent import llm

        captured = []
        with patch.object(llm, "_request", side_effect=lambda config, path, payload, timeout=None: captured.append(payload) or {"message": {}}):
            llm.chat({}, [{"role": "user", "content": "hello"}])
        self.assertEqual(captured[0]["options"]["num_predict"], 192)

    def test_llm_accepts_per_request_generation_limit(self) -> None:
        from voice_agent import llm

        captured = []
        with patch.object(
            llm,
            "_request",
            side_effect=lambda config, path, payload, timeout=None: captured.append(payload) or {"message": {}},
        ):
            llm.chat({}, [{"role": "user", "content": "Explain this"}], num_predict=512)
        self.assertEqual(captured[0]["options"]["num_predict"], 512)

    def test_response_profile_scales_with_request(self) -> None:
        self.assertEqual(response_profile("Hi Jarvis"), "brief")
        self.assertEqual(response_profile("What is the current volume?"), "normal")
        self.assertEqual(response_profile("Explain how the tool loop works in detail"), "detailed")

    def test_detailed_agent_request_uses_detailed_generation_budget(self) -> None:
        agent = Agent({"agent": {"allowed_read_roots": [], "write_roots": []}})
        with patch(
            "voice_agent.agent.llm.chat",
            return_value={"content": "A detailed answer.", "tool_calls": []},
        ) as chat:
            self.assertEqual(agent.run("Explain how the tool loop works"), "A detailed answer.")
        self.assertEqual(chat.call_args.kwargs["num_predict"], 512)

    def test_agent_uses_configured_response_profile_budget(self) -> None:
        agent = Agent(
            {
                "agent": {"allowed_read_roots": [], "write_roots": []},
                "llm": {"response_profiles": {"detailed": {"num_predict": 700}}},
            }
        )
        with patch(
            "voice_agent.agent.llm.chat",
            return_value={"content": "A configured answer.", "tool_calls": []},
        ) as chat:
            self.assertEqual(agent.run("Explain how the tool loop works"), "A configured answer.")
        self.assertEqual(chat.call_args.kwargs["num_predict"], 700)

    def test_list_files_handles_symlinks_on_supported_python(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "folder").mkdir()
            (root / "link").symlink_to(root / "folder", target_is_directory=True)
            config = {"agent": {"allowed_read_roots": [str(root)]}}
            context = ToolContext(config=config, audit=AuditLog(root / "audit.jsonl"), cwd=root)
            listing = _list_files({"path": str(root)}, context)
            self.assertIn("folder  [dir]", listing)
            self.assertIn("link  [link]", listing)

    def test_tool_arguments_are_validated_at_runtime(self) -> None:
        agent = Agent({"agent": {"allowed_read_roots": [], "write_roots": []}})
        self.assertIn("unsupported argument", agent._execute_tool("media", {"action": "play", "extra": "x"}))
        self.assertIn("unsupported value", agent._execute_tool("media", {"action": "launch"}))

    def test_agent_does_not_claim_success_after_unresolved_tool_error(self) -> None:
        agent = Agent({"agent": {"allowed_read_roots": [], "write_roots": []}})
        agent.registry["media"] = Tool(
            name="media",
            description="Test media.",
            parameters={
                "type": "object",
                "properties": {"action": {"type": "string"}},
                "required": ["action"],
                "additionalProperties": False,
            },
            handler=lambda args, context: "ERROR: No media player is running.",
        )
        responses = iter(
            [
                {"content": "", "tool_calls": [{"function": {"name": "media", "arguments": {"action": "play"}}}]},
                {"content": "The song is playing.", "tool_calls": []},
            ]
        )
        with patch("voice_agent.agent.llm.chat", side_effect=lambda *args, **kwargs: next(responses)) as chat:
            self.assertEqual(agent.run("play music"), "No media player is running.")
        self.assertEqual(chat.call_count, 1)

    def test_agent_preserves_complete_turns_between_requests(self) -> None:
        agent = Agent({"agent": {"allowed_read_roots": [], "write_roots": []}})
        requests = []
        responses = iter(
            [
                {"content": "The first answer.", "tool_calls": []},
                {"content": "The second answer.", "tool_calls": []},
            ]
        )

        def fake_chat(_config, messages, **_kwargs):
            requests.append([dict(message) for message in messages])
            return next(responses)

        with patch("voice_agent.agent.llm.chat", side_effect=fake_chat):
            self.assertEqual(agent.run("first question"), "The first answer.")
            self.assertEqual(agent.run("second question"), "The second answer.")

        self.assertEqual(
            [(message["role"], message["content"]) for message in requests[1]],
            [
                ("system", requests[1][0]["content"]),
                ("user", "first question"),
                ("assistant", "The first answer."),
                ("user", "second question"),
            ],
        )

    def test_agent_preserves_tool_turn_between_requests(self) -> None:
        agent = Agent({"agent": {"allowed_read_roots": [], "write_roots": []}})
        agent.registry["probe"] = Tool(
            name="probe",
            description="Test probe.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=lambda args, context: "probe result",
        )
        requests = []
        responses = iter(
            [
                {"content": "", "tool_calls": [{"function": {"name": "probe", "arguments": {}}}]},
                {"content": "The tool result is complete.", "tool_calls": []},
                {"content": "The next answer is independent.", "tool_calls": []},
            ]
        )

        def fake_chat(_config, messages, **_kwargs):
            requests.append([dict(message) for message in messages])
            return next(responses)

        with patch("voice_agent.agent.llm.chat", side_effect=fake_chat):
            self.assertEqual(agent.run("probe something"), "The tool result is complete.")
            self.assertEqual(agent.run("a new question"), "The next answer is independent.")

        self.assertEqual(
            [message["role"] for message in requests[2]],
            ["system", "user", "assistant", "tool", "assistant", "user"],
        )
        self.assertEqual(requests[2][3]["tool_name"], "probe")
        self.assertEqual(requests[2][4]["content"], "The tool result is complete.")

    def test_agent_does_not_retain_orphaned_tool_messages(self) -> None:
        history = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old answer"},
            {"role": "tool", "tool_name": "stale", "content": "stale result"},
        ]
        self.assertEqual(
            Agent._complete_history(history, 4),
            [
                {"role": "user", "content": "old"},
                {"role": "assistant", "content": "old answer"},
            ],
        )

    def test_agent_history_budget_drops_stale_large_tool_turn(self) -> None:
        history = [
            {"role": "user", "content": "old request"},
            {"role": "assistant", "content": "old answer", "tool_calls": [{"function": {"name": "probe"}}]},
            {"role": "tool", "tool_name": "probe", "content": "x" * 5000},
            {"role": "assistant", "content": "old summary"},
            {"role": "user", "content": "recent request"},
            {"role": "assistant", "content": "recent answer"},
        ]
        bounded = Agent._complete_history(history, 24, 100)
        self.assertEqual(
            [(message["role"], message["content"]) for message in bounded],
            [("user", "recent request"), ("assistant", "recent answer")],
        )

    def test_tts_does_not_print_failed_playback(self) -> None:
        with patch.object(tts, "_piper_speak", return_value=False), patch.object(
            tts, "_espeak_speak", return_value=False
        ), patch.object(tts, "find_executable", return_value="piper"), patch.object(
            tts, "which", return_value="espeak-ng"
        ), patch("sys.stdout") as stdout:
            self.assertFalse(
                tts.speak(
                    "A response",
                    {"tts": {"enabled": True, "engine": "piper", "fallback": "espeak-ng"}},
                )
            )
        stdout.write.assert_not_called()

    def test_tts_speaks_complete_long_response_in_chunks(self) -> None:
        text = "First sentence is complete. Second sentence is also complete. Third sentence finishes the answer."
        spoken = []

        def capture(chunk, config, stop_requested=None):
            spoken.append(chunk)
            return True

        with patch.object(tts, "_piper_speak", side_effect=capture):
            self.assertTrue(
                tts.speak(
                    text,
                    {"tts": {"engine": "piper", "fallback": "none", "max_chars": 50}},
                )
            )
        self.assertGreater(len(spoken), 1)
        self.assertEqual(" ".join(spoken), text)
        self.assertTrue(all(not chunk.endswith("sentence") for chunk in spoken[:-1]))

    def test_background_status_speech_does_not_block_dispatch(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def delayed_status(*_args, **_kwargs) -> bool:
            started.set()
            release.wait(timeout=1)
            return True

        with patch.object(cli, "_speak_status", side_effect=delayed_status):
            status_thread = cli._speak_status_background({"tts": {"acknowledgement": True}}, "Okay.")
            self.assertIsNotNone(status_thread)
            self.assertTrue(started.wait(timeout=1))
            self.assertTrue(status_thread.is_alive())
            release.set()
            status_thread.join(timeout=1)
            self.assertFalse(status_thread.is_alive())

    def test_transcript_confirmation_accepts_yes(self) -> None:
        with patch.object(cli.tts, "speak", return_value=True) as speak, patch.object(
            cli, "record_utterance", return_value=Path("/tmp/confirmation.wav")
        ), patch.object(cli.stt, "transcribe", return_value="yes"), patch.object(Path, "unlink"):
            self.assertTrue(cli._confirm_transcript({"stt": {"confirm_transcript": True}}, "What time is it?"))
        self.assertIn("I heard:", speak.call_args.args[0])

    def test_transcript_confirmation_retries_unclear_response(self) -> None:
        answers = iter(["maybe", "no"])
        with patch.object(cli.tts, "speak", return_value=True) as speak, patch.object(
            cli, "record_utterance", side_effect=[Path("/tmp/one.wav"), Path("/tmp/two.wav")]
        ), patch.object(cli.stt, "transcribe", side_effect=lambda _path, _config: next(answers)), patch.object(
            Path, "unlink"
        ):
            self.assertFalse(
                cli._confirm_transcript(
                    {"stt": {"confirm_transcript": True, "confirmation_retries": 1}}, "Play music"
                )
            )
            self.assertEqual(speak.call_count, 2)

    def test_transcript_confirmation_is_disabled_by_default(self) -> None:
        with patch.object(cli.tts, "speak") as speak, patch.object(cli, "record_utterance") as record:
            self.assertTrue(cli._confirm_transcript({"stt": {}}, "What time is it?"))
        speak.assert_not_called()
        record.assert_not_called()

    def test_stt_removes_spaced_inaudible_placeholders(self) -> None:
        self.assertEqual(_clean("[ Inaudible ] ( speaking in a foreign language )"), "")

    def test_agent_does_not_speak_raw_tool_protocol(self) -> None:
        agent = Agent({"agent": {"allowed_read_roots": [], "write_roots": []}})
        with patch(
            "voice_agent.agent.llm.chat",
            return_value={"content": 'ACTION: power\nARGUMENTS: {"action":"poweroff"}', "tool_calls": []},
        ):
            self.assertEqual(agent.run("[inaudible]"), "I couldn't safely execute that request.")

    def test_agent_reports_safe_tool_progress(self) -> None:
        reported = []
        agent = Agent(
            {"agent": {"allowed_read_roots": [], "write_roots": []}},
            status_reporter=reported.append,
        )
        tool = Tool(
            name="probe",
            description="Test probe.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=lambda args, context: "ok",
        )
        agent.registry["probe"] = tool
        self.assertEqual(agent._execute_tool("probe", {}), "ok")
        self.assertEqual(reported, ["probe"])
        tool.dangerous = True
        with patch.object(agent, "_confirm", return_value=False):
            self.assertIn("DENIED", agent._execute_tool("probe", {}))
        self.assertEqual(reported, ["probe"])

    def test_plugin_names_are_validated_before_import(self) -> None:
        config = {"agent": {"plugin_modules": ["__import__"]}}
        with self.assertRaises(RuntimeError):
            build_registry(config, AuditLog(), Path.cwd())

    def test_browser_url_boundary_rejects_unsafe_targets(self) -> None:
        self.assertEqual(_allowed_url("https://example.test/path", ["example.test"]), "https://example.test/path")
        with self.assertRaises(ToolError):
            _allowed_url("http://example.test", ["example.test"])
        with self.assertRaises(ToolError):
            _allowed_url("https://other.test", ["example.test"])
        with self.assertRaises(ToolError):
            _allowed_url("https://user:password@example.test", ["example.test"])

    def test_local_music_track_is_limited_to_music_roots(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            track = root / "song.mp3"
            track.write_bytes(b"audio")
            long_track = root / "Charlie Puth - Attention (Official Video).mp3"
            long_track.write_bytes(b"audio")
            context = ToolContext(
                config={"agent": {"music_roots": [str(root)]}},
                audit=AuditLog(root / "audit.jsonl"),
                cwd=root,
            )
            self.assertEqual(system._music_file("song", context), track)
            self.assertEqual(system._music_file("attention", context), long_track)
            with self.assertRaises(ToolError):
                system._music_file("../song.mp3", context)

    def test_media_starts_configured_player_when_playerctl_has_no_player(self) -> None:
        context = ToolContext(
            config={
                "agent": {
                    "apps": {"elisa": ["elisa"]},
                    "media_player": "elisa",
                    "music_roots": [],
                }
            },
            audit=AuditLog(),
            cwd=Path.cwd(),
        )
        results = iter(
            [
                SimpleNamespace(returncode=0, stdout="", stderr=""),
                SimpleNamespace(returncode=0, stdout="", stderr=""),
            ]
        )
        with patch.object(system, "which", return_value=True), patch.object(
            system, "desktop_popen"
        ), patch.object(
            system, "_mpris_names", side_effect=[[], ["org.mpris.MediaPlayer2.elisa"]]
        ), patch.object(system, "desktop_run", side_effect=lambda *args, **kwargs: next(results)), patch.object(
            system.time, "sleep"
        ):
            self.assertIn("Media play sent", system._media({"action": "play"}, context))

    def test_open_url_launches_configured_browser(self) -> None:
        context = ToolContext(
            config={"agent": {"apps": {"browser": ["firefox"]}}},
            audit=AuditLog(),
            cwd=Path.cwd(),
        )
        with patch.object(system, "which", return_value="/usr/bin/firefox"), patch.object(
            system, "desktop_popen"
        ) as popen:
            self.assertIn("Opened https://example.com", system._open_url({"url": "https://example.com"}, context))
            popen.assert_called_once()
            self.assertEqual(popen.call_args.args[0], ["firefox", "https://example.com"])

    def test_system_info_runs_configured_status_commands(self) -> None:
        context = ToolContext(config={}, audit=AuditLog(), cwd=Path.cwd())

        def fake_run(command, **kwargs):
            output = "Model name: test CPU" if command[0] == "lscpu" else f"ran {' '.join(command)}"
            return SimpleNamespace(returncode=0, stdout=output, stderr="")

        with patch.object(system, "desktop_run", side_effect=fake_run):
            result = system._system_info({"query": "cpu"}, context)
        self.assertIn("cpu:", result)
        self.assertIn("Model name: test CPU", result)

    def test_search_parser_keeps_snippets_and_decodes_redirects(self) -> None:
        html = (
            '<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa">'
            "Example <b>Title</b></a>"
            '<a class="result__snippet">Snippet <b>text</b></a>'
        )
        parser = _SearchParser()
        parser.feed(html)
        parser.close()
        self.assertEqual(parser.results[0]["title"], "Example Title")
        self.assertEqual(parser.results[0]["snippet"], "Snippet text")
        self.assertEqual(parser.results[0]["url"], "https://example.com/a")
        self.assertEqual(_search_result_url("https://example.com/plain"), "https://example.com/plain")

    def test_reminders_are_locked_and_can_be_due(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "reminders.json"
            with patch.object(personal, "_REMINDERS", path):
                created = personal.create_reminder("stand up", "2000-01-01T00:00:00+00:00")
                reminder_id = created.split()[2]
                self.assertIn("stand up", personal.list_reminders())
                self.assertEqual(personal.due_reminders(), ["stand up"])
                self.assertIn("[notified]", personal.list_reminders())
                self.assertIn(reminder_id, personal.cancel_reminder(reminder_id))
                self.assertEqual(personal.list_reminders(), "No local reminders are scheduled.")

    def test_email_drafts_support_create_list_show_and_delete(self) -> None:
        with TemporaryDirectory() as directory:
            draft_dir = Path(directory) / "drafts"
            context = ToolContext(config={}, audit=AuditLog(Path(directory) / "audit.jsonl"), cwd=Path(directory))
            with patch.object(personal, "_EMAIL_DIR", draft_dir):
                result = personal._email_draft(
                    {
                        "action": "create",
                        "to": "one@example.test",
                        "cc": "two@example.test",
                        "subject": "Hello",
                        "body": "Body text",
                    },
                    context,
                )
                self.assertIn("not sent", result)
                draft = next(draft_dir.glob("*.eml"))
                self.assertIn("Hello", personal._email_draft({"action": "list"}, context))
                shown = personal._email_draft({"action": "show", "id": draft.name}, context)
                self.assertIn("Cc: two@example.test", shown)
                self.assertIn("Body text", shown)
                self.assertIn("Deleted", personal._email_draft({"action": "delete", "id": draft.name}, context))
                self.assertFalse(draft.exists())

    def test_tts_engine_and_fallback_are_honored(self) -> None:
        with patch.object(tts, "_piper_speak", return_value=False) as piper, patch.object(
            tts, "_espeak_speak", return_value=True
        ) as espeak:
            self.assertTrue(tts.speak("hello", {"tts": {"engine": "piper", "fallback": "espeak-ng"}}))
            piper.assert_called_once()
            espeak.assert_called_once()
        with patch.object(tts, "_piper_speak", return_value=False) as piper, patch.object(
            tts, "_espeak_speak", return_value=True
        ) as espeak:
            self.assertTrue(tts.speak("hello", {"tts": {"engine": "espeak-ng", "fallback": "none"}}))
            piper.assert_not_called()
            espeak.assert_called_once()
        with patch.object(tts, "_piper_speak", return_value=True) as piper, patch.object(
            tts, "_espeak_speak", return_value=True
        ) as espeak:
            self.assertFalse(tts.speak("hello", {"tts": {"enabled": False}}))
            piper.assert_not_called()
            espeak.assert_not_called()

    def test_fixed_recording_honors_sounddevice_backend(self) -> None:
        result = object()
        config = {"audio": {"backend": "sounddevice"}}
        with patch.object(audio, "_record_sounddevice_fixed", return_value=result) as record:
            self.assertIs(audio.record_fixed(config, 1), result)
        record.assert_called_once_with(16000, "", 1)
        with self.assertRaises(audio.AudioError):
            audio.record_fixed(config, 0)

    def test_fallback_tool_parser_accepts_multiple_calls(self) -> None:
        agent = Agent({"agent": {"allowed_read_roots": [], "write_roots": []}})
        calls = agent._fallback_tool_call(
            '{"tool_calls": [{"function": {"name": "media", "arguments": {"action": "play"}}},'
            ' {"name": "clock", "arguments": {}}]}'
        )
        self.assertEqual([call["function"]["name"] for call in calls], ["media", "clock"])
        self.assertIsNone(agent._fallback_tool_call("not a tool call"))

    def test_file_search_skips_sensitive_directories_and_large_reads(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".ssh").mkdir()
            (root / ".ssh" / "secret.txt").write_text("secret", encoding="utf-8")
            (root / "public.txt").write_text("public", encoding="utf-8")
            large = root / "large.txt"
            with large.open("wb") as handle:
                handle.seek(5_000_001)
                handle.write(b"x")
            config = {"agent": {"allowed_read_roots": [str(root)]}}
            context = ToolContext(config=config, audit=AuditLog(root / "audit.jsonl"), cwd=root)
            found = _search_files({"path": str(root), "pattern": "secret"}, context)
            self.assertIn("No matches", found)
            with self.assertRaises(ToolError):
                _read_file({"path": str(large)}, context)

    def test_file_search_skips_symlinks(self) -> None:
        with TemporaryDirectory() as directory, TemporaryDirectory() as outside_dir:
            root = Path(directory)
            outside = Path(outside_dir)
            (outside / "secret.txt").write_text("secret", encoding="utf-8")
            (root / "secret-link.txt").symlink_to(outside / "secret.txt")
            (root / "outside-link").symlink_to(outside, target_is_directory=True)
            config = {"agent": {"allowed_read_roots": [str(root)]}}
            context = ToolContext(config=config, audit=AuditLog(root / "audit.jsonl"), cwd=root)
            self.assertIn("No matches", _search_files({"path": str(root), "pattern": "secret"}, context))

    def test_atomic_write_applies_requested_mode(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("old", encoding="utf-8")
            path.chmod(0o644)
            atomic_write(path, "new", mode=0o600)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
