import os
import tempfile

# herrington.usage.v1 events from tests are tagged env="test" so they never mix with prod (DAN-233).
os.environ.setdefault("ANALYTICS_ENV", "test")
# Keep the server's module-level database (and its usage counters) out of the developer's data/dojo.db.
os.environ.setdefault("DOJO_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="dojo-tests-"), "dojo.db"))
