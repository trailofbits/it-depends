from inspect import isclass
from pkgutil import iter_modules
from pathlib import Path
from importlib import import_module

# Automatically load all modules in the `it_depends` package,
# so all DependencyClassifiers will auto-register themselves:
package_dir = Path(__file__).resolve().parent
for (_, module_name, _) in iter_modules([str(package_dir)]):
    # import the module and iterate through its attributes
    if module_name != "__main__":
        module = import_module(f"{__name__}.{module_name}")
