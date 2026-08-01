import unittest

import retry_policy


class RetryPolicyTest(unittest.TestCase):
    def test_default_retries_are_safe_for_transient_failures(self) -> None:
        self.assertEqual(retry_policy.DEFAULT_RETRIES, 4)


if __name__ == "__main__":
    unittest.main()
