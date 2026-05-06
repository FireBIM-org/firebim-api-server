import sqlalchemy
from sqlalchemy.dialects import registry
import py_pglite.sqlalchemy

print("Registered dialects after import:")
for name in sorted(registry.impls.keys()):
    print(name)

try:
    import py_pglite
    print("py_pglite imported successfully")
except ImportError:
    print("py_pglite NOT found")

try:
    import py_pglite.sqlalchemy
    print("py_pglite.sqlalchemy imported successfully")
except ImportError:
    print("py_pglite.sqlalchemy NOT found")
