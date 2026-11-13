import os
import sys
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(ROOT), "src"))

loader = unittest.TestLoader()
suite = loader.discover(ROOT, pattern="test_*.py", top_level_dir=os.path.dirname(ROOT))
if __name__ == "__main__":
    sys.exit(0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1)
