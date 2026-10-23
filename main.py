import os
import sys

import decky  # type: ignore

# The loader appends py_modules to sys.path; older Windows builds did not, so make sure.
_PY_MODULES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "py_modules")
if _PY_MODULES not in sys.path:
    sys.path.append(_PY_MODULES)

from overdrive import device  # noqa: E402


class Plugin:
    async def _main(self) -> None:
        decky.logger.info("Overdrive started")

    async def _unload(self) -> None:
        decky.logger.info("Overdrive unloaded")

    async def get_support(self) -> dict:
        product = device.product_name()
        return {"supported": device.is_ally(lambda: product), "product": product}
