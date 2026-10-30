import unittest

from voice_agent.tools.files import ToolError, _validate_check
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


if __name__ == "__main__":
    unittest.main()
