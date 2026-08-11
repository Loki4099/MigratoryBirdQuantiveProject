import unittest

from style_rotation.ops.environment import (
    capture_numerical_environment,
    numerical_environment_hash,
)


class NumericalEnvironmentTests(unittest.TestCase):
    def test_capture_is_stable_and_hashable(self) -> None:
        environment = capture_numerical_environment(("numpy", "definitely-not-installed"))
        self.assertEqual(environment["packages"]["definitely-not-installed"], None)
        self.assertEqual(
            numerical_environment_hash(environment), numerical_environment_hash(environment)
        )
        self.assertEqual(len(numerical_environment_hash(environment)), 64)


if __name__ == "__main__":
    unittest.main()
