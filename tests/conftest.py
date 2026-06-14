import pathlib
import sys

import pytest

# Ensure project root is in sys.path for imports in tests
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from partita_bot.storage import Database


@pytest.fixture
def admin_test_env():
    import partita_bot.admin as admin_module

    original_db = admin_module.db
    test_db = Database(database_url="sqlite:///:memory:")
    admin_module.db = test_db
    admin_module.app.secret_key = "test-secret"
    yield admin_module, test_db
    admin_module.db = original_db
    test_db.close()
