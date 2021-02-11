from os import chdir, getcwd
from typing import List

from pip._internal.commands.install import (
    get_requirement_tracker,
    InstallCommand,
    make_target_python,
    reject_location_related_install_options,
    TempDirectory,
    WheelCache
)

from pip._internal.utils.temp_dir import global_tempdir_manager, tempdir_registry

from .dependencies import Dependency, Package


def get_dependencies(pip_package_path: str) -> List[Package]:
    orig_dir = getcwd()
    chdir(pip_package_path)

    try:
        install = InstallCommand("install", "")

        with install.main_context():
            options, args = install.parse_args(["."])

            upgrade_strategy = "to-satisfy-only"

            install.enter_context(tempdir_registry())
            # Intentionally set as early as possible so globally-managed temporary
            # directories are available to the rest of the code.
            install.enter_context(global_tempdir_manager())

            session = install.get_default_session(options)

            target_python = make_target_python(options)
            finder = install._build_package_finder(
                options=options,
                session=session,
                target_python=target_python,
                ignore_requires_python=options.ignore_requires_python,
            )
            wheel_cache = WheelCache(options.cache_dir, options.format_control)

            req_tracker = install.enter_context(get_requirement_tracker())

            directory = TempDirectory(
                delete=not options.no_clean,
                kind="install",
                globally_managed=True,
            )

            reqs = install.get_requirements(args, options, finder, session)

            reject_location_related_install_options(
                reqs, options.install_options
            )

            preparer = install.make_requirement_preparer(
                temp_build_dir=directory,
                options=options,
                req_tracker=req_tracker,
                session=session,
                finder=finder,
                use_user_site=options.use_user_site,
            )
            resolver = install.make_resolver(
                preparer=preparer,
                finder=finder,
                options=options,
                wheel_cache=wheel_cache,
                use_user_site=options.use_user_site,
                ignore_installed=True,
                ignore_requires_python=options.ignore_requires_python,
                force_reinstall=options.force_reinstall,
                upgrade_strategy=upgrade_strategy,
                use_pep517=options.use_pep517,
            )

            install.trace_basic_info(finder)

            requirement_set = resolver.resolve(
                reqs, check_supported_wheels=not options.target_dir
            )

            package_map = {}

            for install_requirement in requirement_set.all_requirements:
                if install_requirement.comes_from is None:
                    # This is the root (pip deps .)
                    continue
                elif type(install_requirement.comes_from) is str:
                    # This is a requirement file (pip deps -r requirements.txt)
                    package_name = "requirements.txt"
                    package_version = ""
                else:
                    package_name = install_requirement.comes_from.name
                    package_version = str(install_requirement.comes_from.req.specifier)

                if package_name in package_map:
                    package = package_map[package_name]
                else:
                    package = {
                        "version": package_version,
                        "package": package_name,
                        "dependencies": []
                    }

                dependency = {
                    "package": install_requirement.name,
                    "version": str(install_requirement.req.specifier),
                    "locked": str(install_requirement.is_pinned).lower()
                }
                package["dependencies"].append(dependency)

                package_map[package_name] = package

            return [Package.load(package) for package in package_map.values()]
    finally:
        chdir(orig_dir)
