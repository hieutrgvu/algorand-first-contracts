import pytest

from algosdk.future import transaction

from helpers import (
    call_sandbox_command,
    add_standalone_account,
    compile_teal_source,
    suggested_params,
    send_transactions,
    fund_accounts,
    get_app_global_state,
    opt_in_app,
)

from counter_contract import approval_program, clear_state_program


def setup_module(module):
    """Ensure Algorand Sandbox is up prior to running tests from this module."""
    call_sandbox_command("up")


class TestCounterContract:
    """Class for testing the counter contract."""

    def setup_class(self):
        self.deployer = add_standalone_account()
        self.users = [add_standalone_account() for i in range(2)]

        print()
        print("init fund for deployer, users")
        fund_accounts(
            [self.deployer] + self.users,
            [5_000_000] * (1 + len(self.users)),
        )

    def _create(self):
        txn = transaction.ApplicationCreateTxn(
            sender=self.deployer.get("address"),
            on_complete=transaction.OnComplete.NoOpOC,
            approval_program=compile_teal_source(approval_program()),
            clear_program=compile_teal_source(clear_state_program()),
            global_schema=transaction.StateSchema(num_uints=1, num_byte_slices=0),
            local_schema=transaction.StateSchema(num_uints=0, num_byte_slices=0),
            sp=suggested_params(),
        )
        return send_transactions(self.deployer, [txn]).get("application-index")

    def _add(self, sender, app_id):
        self._noop_call(sender, app_id, b"Add")

    def _deduct(self, sender, app_id):
        self._noop_call(sender, app_id, b"Deduct")

    def _noop_call(self, sender, app_id, method):
        txn = transaction.ApplicationCallTxn(
            sender=sender.get("address"),
            index=app_id,
            on_complete=transaction.OnComplete.NoOpOC,
            app_args=[method],
            accounts=[sender.get("address")],
            sp=suggested_params(),
        )
        send_transactions(sender, [txn])

    def test_add_deduct(self):
        print("deployer creates app")
        app_id = self._create()

        print("user adds")
        self._add(self.users[0], app_id)

        print("user adds")
        self._add(self.users[1], app_id)

        print("user adds")
        self._add(self.users[0], app_id)
        app_global_state = get_app_global_state(app_id)
        assert app_global_state[b"Count"] == 3

        print("user deducts")
        self._deduct(self.users[1], app_id)

        print("user deducts")
        self._deduct(self.users[1], app_id)
        app_global_state = get_app_global_state(app_id)
        assert app_global_state[b"Count"] == 1

    def test_deduct_below_zero(self):
        print("deployer creates app")
        app_id = self._create()

        print("user deducts counter to below zero but cannot")
        self._deduct(self.users[0], app_id)
        app_global_state = get_app_global_state(app_id)
        assert app_global_state[b"Count"] == 0

        print("users add then try to deduct more than add")
        self._add(self.users[1], app_id)
        self._deduct(self.users[0], app_id)
        self._deduct(self.users[1], app_id)
        app_global_state = get_app_global_state(app_id)
        assert app_global_state[b"Count"] == 0

    def test_two_adds(self):
        print("deployer creates app")
        app_id = self._create()

        print("users opt in to app but cannot, app does not hold local state")
        with pytest.raises(Exception):
            opt_in_app(self.users, app_id)

        print("user tries to add 2 times in a transaction group")
        txn_1 = transaction.ApplicationCallTxn(
            sender=self.users[0].get("address"),
            index=app_id,
            on_complete=transaction.OnComplete.NoOpOC,
            app_args=[b"add"],
            accounts=[self.users[0].get("address")],
            sp=suggested_params(),
            note="first",
        )
        txn_2 = transaction.ApplicationCallTxn(
            sender=self.users[0].get("address"),
            index=app_id,
            on_complete=transaction.OnComplete.NoOpOC,
            app_args=[b"add"],
            accounts=[self.users[0].get("address")],
            sp=suggested_params(),
            note="second",
        )
        with pytest.raises(Exception):
            send_transactions(self.users[0], [txn_1, txn_2])
