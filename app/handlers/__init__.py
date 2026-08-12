"""Handler package bootstrap.

The GitHub Import freshness guard is installed before individual handlers import
service callables. This keeps cached remote inventory fast while forcing local
import history to be re-applied after a same-package lock wait.
"""

from app.services.github_import_freshness import install_github_import_freshness_guard

install_github_import_freshness_guard()
