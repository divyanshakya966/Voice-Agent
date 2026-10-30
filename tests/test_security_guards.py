import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from voice_agent import cli
from voice_agent.agent import Agent
from voice_agent.audit import AuditLog
from voice_agent.tools.base import ToolContext
from voice_agent.tools.files import ToolError, _list_files, _validate_check
from voice_agent.util import redact


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

    def test_llm_default_generation_limit_is_bounded(self) -> None:
        from voice_agent import llm

        captured = []
        with patch.object(llm, "_request", side_effect=lambda config, path, payload, timeout=None: captured.append(payload) or {"message": {}}):
            llm.chat({}, [{"role": "user", "content": "hello"}])
        self.assertEqual(captured[0]["options"]["num_predict"], 192)

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


if __name__ == "__main__":
    unittest.main()
