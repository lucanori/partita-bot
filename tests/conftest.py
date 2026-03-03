import pathlib
import sys

# Ensure project root is in sys.path for imports in tests
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
