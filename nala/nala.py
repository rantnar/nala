#                 __
#    ____ _____  |  | _____
#   /    \\__  \ |  | \__  \
#  |   |  \/ __ \|  |__/ __ \_
#  |___|  (____  /____(____  /
#       \/     \/          \/
#
# Copyright (C) 2010 Tatsuhiro Tsujikawa
# Copyright (C) 2021, 2022 Blake Lee
#
# This file is part of nala
# nala is based upon apt-metalink https://github.com/tatsuhiro-t/apt-metalink
#
# nala is program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# nala is program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with nala.  If not, see <https://www.gnu.org/licenses/>.
"""Main module for Nala which facilitates apt."""
from __future__ import annotations

import re
import sys
from typing import Generator, Optional

import apt_pkg
import typer
from apt.package import Package, Version

from nala import _, color
from nala.cache import Cache
from nala.constants import (
	ARCHIVE_DIR,
	CAT_ASCII,
	ERROR_PREFIX,
	LISTS_DIR,
	LISTS_PARTIAL_DIR,
	NALA_SOURCES,
	PARTIAL_DIR,
	PKGCACHE,
	SRCPKGCACHE,
)
from nala.error import broken_error, broken_pkg, pkg_error, unmarked_error
from nala.install import (
	auto_remover,
	check_broken,
	check_state,
	check_term_ask,
	fix_excluded,
	get_changes,
	install_local,
	package_manager,
	setup_cache,
	split_local,
)
from nala.options import (
	ALL_ARCHES,
	ALL_VERSIONS,
	ASSUME_YES,
	AUTO_REMOVE,
	DEBUG,
	DOWNLOAD_ONLY,
	FETCH,
	FIX_BROKEN,
	FULL,
	INSTALLED,
	LISTS,
	NAMES,
	OPTION,
	PURGE,
	RAW_DPKG,
	RECOMMENDS,
	REMOVE_ESSENTIAL,
	SUGGESTS,
	UPDATE,
	UPGRADABLE,
	UPGRADEABLE,
	VERBOSE,
	VIRTUAL,
	_doc,
	arguments,
	nala,
)
from nala.search import iter_search, search_name
from nala.show import additional_notice, pkg_not_found, show_main
from nala.utils import (
	PackageHandler,
	arg_check,
	ask,
	dprint,
	eprint,
	get_version,
	iter_remove,
	pkg_installed,
	sudo_check,
	vprint,
)

nala_pkgs = PackageHandler()


@_doc
@nala.command("update")
# pylint: disable=unused-argument
def _update(
	debug: bool = DEBUG,
	raw_dpkg: bool = RAW_DPKG,
	dpkg_option: list[str] = OPTION,
	verbose: bool = VERBOSE,
) -> None:
	"""Update package list."""
	sudo_check()
	arguments.update = True
	setup_cache().print_upgradable()


@_doc
@nala.command()
# pylint: disable=unused-argument,too-many-arguments, too-many-locals
def upgrade(
	exclude: Optional[list[str]] = typer.Option(
		None,
		metavar="PKG",
		help=_("Specify packages to exclude during upgrade. Accepts glob*"),
	),
	purge: bool = PURGE,
	debug: bool = DEBUG,
	raw_dpkg: bool = RAW_DPKG,
	download_only: bool = DOWNLOAD_ONLY,
	remove_essential: bool = REMOVE_ESSENTIAL,
	update: bool = UPDATE,
	auto_remove: bool = AUTO_REMOVE,
	install_recommends: bool = RECOMMENDS,
	install_suggests: bool = SUGGESTS,
	fix_broken: bool = FIX_BROKEN,
	full: bool = typer.Option(
		True, help=_("Toggle runs a normal upgrade instead of full-upgrade")
	),
	dpkg_option: list[str] = OPTION,
	assume_yes: bool = ASSUME_YES,
	verbose: bool = VERBOSE,
) -> None:
	"""Update package list and upgrade the system."""
	sudo_check()

	def _upgrade(
		full: bool = True,
		exclude: list[str] | None = None,
		nested_cache: Cache | None = None,
	) -> None:
		"""Upgrade pkg[s]."""
		cache = nested_cache or setup_cache()
		check_state(cache, nala_pkgs)

		is_upgrade = cache.upgradable_pkgs()
		protected = cache.protect_upgrade_pkgs(exclude)
		try:
			cache.upgrade(dist_upgrade=full)
		except apt_pkg.Error as error:
			if exclude:
				exclude = fix_excluded(protected, is_upgrade)
				if ask(_("Would you like us to protect these and try again?")):
					cache.clear()
					_upgrade(full, exclude, cache)
					sys.exit()
				sys.exit(
					_("{error} You have held broken packages").format(
						error=ERROR_PREFIX
					)
				)
			raise error from error

		if kept_back := [pkg for pkg in is_upgrade if not pkg.is_upgradable]:
			cache.clear()
			print(color(_("The following packages were kept back:"), "YELLOW"))
			for pkg in kept_back:
				broken_pkg(pkg, cache)
			check_term_ask()
			cache.upgrade(dist_upgrade=arguments.full)

		auto_remover(cache, nala_pkgs)
		get_changes(cache, nala_pkgs, upgrade=True)

	_upgrade(full, exclude)


@_doc
@nala.command()
# pylint: disable=unused-argument,too-many-arguments
def install(
	ctx: typer.Context,
	pkg_names: Optional[list[str]] = typer.Argument(
		None, metavar="PKGS ...", help=_("Package(s) to install")
	),
	purge: bool = PURGE,
	debug: bool = DEBUG,
	raw_dpkg: bool = RAW_DPKG,
	download_only: bool = DOWNLOAD_ONLY,
	remove_essential: bool = REMOVE_ESSENTIAL,
	update: bool = UPDATE,
	auto_remove: bool = AUTO_REMOVE,
	install_recommends: bool = RECOMMENDS,
	install_suggests: bool = SUGGESTS,
	fix_broken: bool = FIX_BROKEN,
	dpkg_option: list[str] = OPTION,
	assume_yes: bool = ASSUME_YES,
	verbose: bool = VERBOSE,
) -> None:
	"""Install packages."""
	_install(pkg_names, ctx)


def _install(pkg_names: list[str] | None, ctx: typer.Context) -> None:
	sudo_check(pkg_names)
	if not pkg_names:
		if arguments.fix_broken:
			_fix_broken()
			return
		ctx.fail(_("{error} Missing packages to install").format(error=ERROR_PREFIX))
	pkg_names = arg_check(pkg_names)
	cache = setup_cache()
	check_state(cache, nala_pkgs)
	not_exist = split_local(pkg_names, cache, nala_pkgs.local_debs)
	install_local(nala_pkgs, cache)

	pkg_names = cache.glob_filter(pkg_names)
	pkg_names = cache.virtual_filter(pkg_names)
	broken, not_found, ver_failed = check_broken(pkg_names, cache)
	not_found.extend(not_exist)

	if not_found or ver_failed:
		pkg_error(not_found, cache, terminate=True)

	pkgs = [cache[pkg_name] for pkg_name in pkg_names]
	if (
		not package_manager(pkg_names, cache)
		# We also check to make sure that all the packages are still
		# Marked upgrade or install after the package manager is run
		or not all(
			(pkg.marked_upgrade or pkg.marked_install or pkg.marked_downgrade)
			for pkg in pkgs
		)
	) and not broken_error(broken, cache):
		unmarked_error(pkgs)

	auto_remover(cache, nala_pkgs)
	get_changes(cache, nala_pkgs)


@nala.command(help=_("Remove packages."))
@nala.command("purge", help=_("Purge packages."))
# pylint: disable=unused-argument,too-many-arguments
def remove(
	pkg_names: list[str] = typer.Argument(
		..., metavar="PKGS ...", help=_("Package(s) to remove/purge")
	),
	purge: bool = PURGE,
	debug: bool = DEBUG,
	raw_dpkg: bool = RAW_DPKG,
	download_only: bool = DOWNLOAD_ONLY,
	remove_essential: bool = REMOVE_ESSENTIAL,
	auto_remove: bool = AUTO_REMOVE,
	fix_broken: bool = FIX_BROKEN,
	update: bool = UPDATE,
	dpkg_option: list[str] = OPTION,
	assume_yes: bool = ASSUME_YES,
	verbose: bool = VERBOSE,
) -> None:
	"""Remove or Purge packages."""
	_remove(pkg_names)


def _remove(pkg_names: list[str]) -> None:
	sudo_check()
	arg_check(pkg_names)
	cache = setup_cache()
	check_state(cache, nala_pkgs)

	_purge = True if arguments.purge else arguments.command == "purge"
	pkg_names = cache.glob_filter(pkg_names)
	pkg_names = cache.virtual_filter(pkg_names)
	broken, not_found, ver_failed = check_broken(
		pkg_names, cache, remove=True, purge=_purge
	)

	if not_found or ver_failed:
		pkg_error(not_found, cache, remove=True)

	if not package_manager(pkg_names, cache, remove=True, purge=_purge):
		broken_error(
			broken,
			cache,
			tuple(
				pkg
				for pkg in cache
				if pkg.is_installed and pkg_installed(pkg).dependencies
			),
		)

	auto_remover(cache, nala_pkgs, _purge)
	get_changes(cache, nala_pkgs, remove=True)


@nala.command("autoremove", help=_("Autoremove packages that are no longer needed."))
@nala.command("autopurge", help=_("Autopurge packages that are no longer needed."))
# pylint: disable=unused-argument,too-many-arguments
def _auto_remove(
	purge: bool = PURGE,
	debug: bool = DEBUG,
	raw_dpkg: bool = RAW_DPKG,
	download_only: bool = DOWNLOAD_ONLY,
	remove_essential: bool = REMOVE_ESSENTIAL,
	update: bool = UPDATE,
	fix_broken: bool = FIX_BROKEN,
	dpkg_option: list[str] = OPTION,
	assume_yes: bool = ASSUME_YES,
	verbose: bool = VERBOSE,
) -> None:
	"""Command for autoremove."""
	sudo_check()
	cache = setup_cache()
	_purge = True if arguments.purge else arguments.command == "autopurge"
	check_state(cache, nala_pkgs)
	auto_remover(cache, nala_pkgs, _purge)
	get_changes(cache, nala_pkgs, remove=True)


def _fix_broken(nested_cache: Cache | None = None) -> None:
	"""Attempt to fix broken packages, if any."""
	cache = nested_cache or setup_cache()
	print("Fixing Broken Packages...")
	cache.fix_broken()

	if nested_cache:
		print(color(_("There are broken packages that need to be fixed!"), "YELLOW"))
		print(
			_("You can use {switch} if you'd like to try without fixing them.").format(
				switch=color("--no-fix-broken", "YELLOW")
			)
		)
	else:
		check_state(cache, nala_pkgs)
	get_changes(cache, nala_pkgs)


@_doc
@nala.command()
# pylint: disable=unused-argument
def show(
	pkg_names: list[str] = typer.Argument(..., help=_("Package(s) to show")),
	debug: bool = DEBUG,
	verbose: bool = VERBOSE,
	all_versions: bool = ALL_VERSIONS,
) -> None:
	"""Show package details."""
	cache = Cache()
	not_found: list[str] = []
	pkg_names = cache.glob_filter(pkg_names)
	pkg_names = cache.virtual_filter(pkg_names)
	additional_records = 0
	for num, pkg_name in enumerate(pkg_names):
		if pkg_name in cache:
			pkg = cache[pkg_name]
			additional_records += show_main(num, pkg)
			continue
		pkg_not_found(pkg_name, cache, not_found)

	if additional_records and not arguments.all_versions:
		additional_notice(additional_records)

	if not_found:
		for error in not_found:
			eprint(error)
		sys.exit(1)


@_doc
@nala.command()
# pylint: disable=unused-argument,too-many-arguments,too-many-locals
def search(
	regex: str = typer.Argument(..., help=_("Regex or word to search for")),
	debug: bool = DEBUG,
	full: bool = FULL,
	names: bool = NAMES,
	installed: bool = INSTALLED,
	upgradable: bool = UPGRADABLE,
	upgradeable: bool = UPGRADEABLE,
	all_versions: bool = ALL_VERSIONS,
	all_arches: bool = ALL_ARCHES,
	virtual: bool = VIRTUAL,
	verbose: bool = VERBOSE,
) -> None:
	"""Search package names and descriptions."""
	cache = Cache()
	found: list[tuple[Package, Version]] = []
	if regex == "*":
		regex = ".*"
	try:
		search_pattern = re.compile(regex, re.IGNORECASE)
	except re.error as error:
		sys.exit(
			_(
				"{error} failed regex compilation '{error_msg} at position {position}"
			).format(error=ERROR_PREFIX, error_msg=error.msg, position=error.pos)
		)
	arches = apt_pkg.get_architectures()
	for pkg in cache:
		if arguments.installed and not pkg.installed:
			continue
		if arguments.upgradable and not pkg.is_upgradable:
			continue
		if arguments.virtual and not cache.is_virtual_package(pkg.name):
			continue
		if arguments.all_arches or pkg.architecture() in arches:
			search_name(pkg, search_pattern, found)

	if not found:
		sys.exit(
			_("{error} {regex} not found.").format(error=ERROR_PREFIX, regex=regex)
		)
	iter_search(found)


@_doc
@nala.command("list")
# pylint: disable=unused-argument,too-many-arguments
def list_pkgs(
	pkg_names: Optional[list[str]] = typer.Argument(
		None, help=_("Package(s) to list.")
	),
	debug: bool = DEBUG,
	full: bool = FULL,
	installed: bool = INSTALLED,
	upgradable: bool = UPGRADABLE,
	upgradeable: bool = UPGRADEABLE,
	all_versions: bool = ALL_VERSIONS,
	virtual: bool = VIRTUAL,
	verbose: bool = VERBOSE,
) -> None:
	"""List packages based on package names."""
	cache = Cache()

	def _list_gen() -> Generator[
		tuple[Package, Version | tuple[Version, ...]], None, None
	]:
		"""Generate to speed things up."""
		for pkg in cache:
			if arguments.installed and not pkg.installed:
				continue
			if arguments.upgradable and not pkg.is_upgradable:
				continue
			if arguments.virtual and not cache.is_virtual_package(pkg.name):
				continue
			if pkg_names:
				if pkg.shortname in pkg_names:
					yield (pkg, get_version(pkg))
				continue
			yield (pkg, get_version(pkg))

	if not iter_search(_list_gen()):
		sys.exit(_("Nothing was found to list."))


@_doc
@nala.command()
# pylint: disable=unused-argument
def clean(
	lists: bool = LISTS,
	fetch: bool = FETCH,
	debug: bool = DEBUG,
	verbose: bool = VERBOSE,
) -> None:
	"""Clear out the local archive of downloaded package files."""
	sudo_check()
	if lists:
		iter_remove(LISTS_DIR)
		iter_remove(LISTS_PARTIAL_DIR)
		print(_("Package lists have been cleaned"))
		return
	if fetch:
		NALA_SOURCES.unlink(missing_ok=True)
		print(_("Nala sources.list has been cleaned"))
		return
	iter_remove(ARCHIVE_DIR)
	iter_remove(PARTIAL_DIR)
	iter_remove(LISTS_PARTIAL_DIR)
	vprint(
		_("Removing {cache}\nRemoving {src_cache}").format(
			cache=PKGCACHE, src_cache=SRCPKGCACHE
		)
	)

	PKGCACHE.unlink(missing_ok=True)
	SRCPKGCACHE.unlink(missing_ok=True)
	print(_("Cache has been cleaned"))


@nala.command(hidden=True)
# pylint: disable=unused-argument
def moo(
	moos: Optional[list[str]] = typer.Argument(None, hidden=True),
	debug: bool = typer.Option(
		False, "--debug", callback=arguments.set_debug, is_eager=True, hidden=True
	),
	update: bool = typer.Option(None, hidden=True),
) -> None:
	"""I beg, pls moo."""
	moo_count = moos.count("moo") if moos else 0
	dprint(f"moo number is {moos}")
	if moo_count == 1:
		print(CAT_ASCII["2"])
	elif moo_count == 2:
		print(CAT_ASCII["3"])
	else:
		print(CAT_ASCII["1"])
	can_no_moo = _("I can't moo for I'm a cat")
	print(f'..."{can_no_moo}"...')
	if update:
		what_did_you_expect = _("What did you expect to update?")
		print(f'..."{what_did_you_expect}"...')
		return
	if update is not None:
		what_did_you_expect = _("What did you expect no-update to do?")
		print(f'..."{what_did_you_expect}"...')
