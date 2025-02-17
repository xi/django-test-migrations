from typing import Optional

from django.conf import settings
from django.core.management import call_command
from django.core.management.color import no_style
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.state import ProjectState

from django_test_migrations import sql
from django_test_migrations.logic.migrations import normalize
from django_test_migrations.plan import truncate_plan
from django_test_migrations.signals import mute_migrate_signals
from django_test_migrations.types import MigrationPlan, MigrationSpec


class Migrator(object):
    """
    Class to manage your migrations and app state.

    It is designed to be used inside the tests to ensure that migrations
    are working as intended: both data and schema migrations.

    This class can be but probably should not be used directly.
    Because we have utility test framework
    integrations for ``unitest`` and ``pytest``.

    Use them for better experience.
    """

    def __init__(
        self,
        database: Optional[str] = None,
    ) -> None:
        """That's where we initialize all required internals."""
        if database is None:
            database = DEFAULT_DB_ALIAS

        self._database: str = database
        self._executor = MigrationExecutor(connections[self._database])

    def apply_initial_migration(self, targets: MigrationSpec) -> ProjectState:
        """Reverse back to the original migration."""
        targets = normalize(targets)

        style = no_style()
        # start from clean database state
        sql.drop_models_tables(self._database, style)
        sql.flush_django_migrations_table(self._database, style)

        # prepare as broad plan as possible based on full plan
        self._executor.loader.build_graph()  # reload
        full_plan = self._executor.migration_plan(
            self._executor.loader.graph.leaf_nodes(),
            clean_start=True,
        )
        plan = truncate_plan(targets, full_plan)

        # apply all migrations from generated plan on clean database
        # (only forward, so any unexpected migration won't be applied)
        # to restore database state before tested migration
        return self._migrate(targets, plan=plan)

    def apply_tested_migration(self, targets: MigrationSpec) -> ProjectState:
        """Apply the next migration."""
        self._executor.loader.build_graph()  # reload
        return self._migrate(normalize(targets))

    def reset(self) -> None:
        """
        Reset the state to the most recent one.

        Notably, signals are not muted here to avoid
        https://github.com/wemake-services/django-test-migrations/issues/128

        """
        class DisableMigrations:
            def __contains__(self, item: str) -> bool:
                return True

            def __getitem__(self, item: str) -> None:
                return None

        pre_migration_modules = settings.MIGRATION_MODULES
        try:
            settings.MIGRATION_MODULES = DisableMigrations()
            call_command(
                'migrate',
                verbosity=0,
                database=self._database,
                run_syncdb=True,
            )
        finally:
            settings.MIGRATION_MODULES = pre_migration_modules

    def _migrate(
        self,
        migration_targets: MigrationSpec,
        plan: Optional[MigrationPlan] = None,
    ) -> ProjectState:
        with mute_migrate_signals():
            return self._executor.migrate(migration_targets, plan=plan)
