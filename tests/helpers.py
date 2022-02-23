"""Module containing helper functions for accessing Algorand blockchain."""

import base64
import os
import pty
import subprocess
from pathlib import Path

from algosdk import account, mnemonic
from algosdk.future import transaction
from algosdk.v2client import algod, indexer

INDEXER_TIMEOUT = 10  # 61 for devMode


## SANDBOX
def _cli_passphrase_for_account(address):
    """Return passphrase for provided address."""
    process = call_sandbox_command("goal", "account", "export", "-a", address)

    if process.stderr:
        raise RuntimeError(process.stderr.decode("utf8"))

    passphrase = ""
    parts = process.stdout.decode("utf8").split('"')
    if len(parts) > 1:
        passphrase = parts[1]
    if passphrase == "":
        raise ValueError(
            "Can't retrieve passphrase from the address: %s\nOutput: %s"
            % (address, process.stdout.decode("utf8"))
        )
    return passphrase


def _sandbox_directory():
    """Return full path to Algorand's sandbox executable.

    The location of sandbox directory is retrieved either from the SANDBOX_DIR
    environment variable or if it's not set then the location of sandbox directory
    is implied to be the sibling of this Django project in the directory tree.
    """
    return os.environ.get("SANDBOX_DIR") or str(
        Path(__file__).resolve().parent.parent / "sandbox"
    )


def _sandbox_executable():
    """Return full path to Algorand's sandbox executable."""
    return _sandbox_directory() + "/sandbox"


def call_sandbox_command(*args):
    """Call and return sandbox command composed from provided arguments."""
    return subprocess.run(
        [_sandbox_executable(), *args], stdin=pty.openpty()[1], capture_output=True
    )


## CLIENTS
def _algod_client():
    """Instantiate and return Algod client object."""
    algod_address = "http://localhost:4001"
    algod_token = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    return algod.AlgodClient(algod_token, algod_address)


def _indexer_client():
    """Instantiate and return Indexer client object."""
    indexer_address = "http://localhost:8980"
    indexer_token = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    return indexer.IndexerClient(indexer_token, indexer_address)


## TRANSACTIONS
def _wait_for_confirmation(client, transaction_id, timeout=5):
    """
    Wait until the transaction is confirmed or rejected, or until 'timeout'
    number of rounds have passed.
    Args:
        transaction_id (str): the transaction to wait for
        timeout (int): maximum number of rounds to wait
    Returns:
        dict: pending transaction information, or throws an error if the transaction
            is not confirmed or rejected in the next timeout rounds
    """
    start_round = client.status()["last-round"] + 1
    current_round = start_round

    while current_round < start_round + timeout:
        try:
            pending_txn = client.pending_transaction_info(transaction_id)
        except Exception:
            return
        if pending_txn.get("confirmed-round", 0) > 0:
            return pending_txn
        elif pending_txn["pool-error"]:
            raise Exception("pool error: {}".format(pending_txn["pool-error"]))
        client.status_after_block(current_round)
        current_round += 1
    raise Exception(
        "pending tx not found in timeout rounds, timeout value = : {}".format(timeout)
    )


def suggested_params():
    """Return the suggested params from the algod client."""
    return _algod_client().suggested_params()


def send_transactions(sender, txns):
    """
    Send transaction to network and wait for confirmation.
    Args:
        sender: dict{str, str} - private key, account address
        txn: transaction
    """
    if len(txns) == 0:
        return

    client = _algod_client()
    if len(txns) == 1:
        signed_txn = txns[0].sign(sender.get("private_key"))
        client.send_transaction(signed_txn)
        return _wait_for_confirmation(client, signed_txn.get_txid())

    transaction.assign_group_id(txns)
    signed_txns = [txn.sign(sender.get("private_key")) for txn in txns]
    transaction_id = client.send_transactions(signed_txns)
    return _wait_for_confirmation(client, transaction_id)


def fund_accounts(addresses, funds):
    """Fund provided `addresses` with `funds` amount of microAlgos."""
    funder = _initial_funds_address()
    if funder is None:
        raise Exception("Initial funds weren't transferred!")

    num_addr = len(addresses)
    if len(funds) != num_addr or num_addr == 0:
        raise Exception("number of addresses and intial funds are not the same")

    client = _algod_client()
    params = client.suggested_params()
    funder_private_key = mnemonic.to_private_key(_cli_passphrase_for_account(funder))

    if num_addr == 1:
        txn = transaction.PaymentTxn(
            funder, params, addresses[0].get("address"), funds[0]
        )
        signed_txn = txn.sign(funder_private_key)
        transaction_id = client.send_transaction(signed_txn)
        _wait_for_confirmation(client, transaction_id)
        return

    txns = [
        transaction.PaymentTxn(funder, params, addresses[i].get("address"), funds[i])
        for i in range(num_addr)
    ]
    transaction.assign_group_id(txns)
    signed_txns = [txn.sign(funder_private_key) for txn in txns]
    transaction_id = client.send_transactions(signed_txns)
    _wait_for_confirmation(client, transaction_id)


def opt_in_app(accounts, app_id):
    if len(accounts) == 0:
        return

    client = _algod_client()
    params = client.suggested_params()
    if len(accounts) == 1:
        txn = transaction.ApplicationOptInTxn(
            accounts[0].get("address"), params, app_id
        )
        signed_txn = txn.sign(accounts[0].get("private_key"))
        transaction_id = client.send_transaction(signed_txn)
        _wait_for_confirmation(client, transaction_id)
        return

    txns = [
        transaction.ApplicationOptInTxn(a.get("address"), params, app_id)
        for a in accounts
    ]
    transaction.assign_group_id(txns)
    signed_txns = [
        txns[i].sign(accounts[i].get("private_key")) for i in range(len(accounts))
    ]
    transaction_id = client.send_transactions(signed_txns)
    _wait_for_confirmation(client, transaction_id)


## CREATING
def add_standalone_account():
    """Create standalone account and return dict of its private key and address."""
    private_key, address = account.generate_account()
    return {"private_key": private_key, "address": address}


## RETRIEVING
def _initial_funds_address():
    """Get the address of initially created account having enough funds.

    Such an account is used to transfer initial funds for the accounts
    created in this tutorial.
    """
    return next(
        (
            account.get("address")
            for account in _indexer_client().accounts().get("accounts", [{}, {}])
            if account.get("created-at-round") == 0
            and account.get("status") == "Offline"  # "Online" for devMode
        ),
        None,
    )


def get_app_global_state(app_id):
    client = _algod_client()
    app_info = client.application_info(app_id)

    state = {}
    for pair in app_info["params"]["global-state"]:
        key = base64.b64decode(pair["key"])
        value = pair["value"]
        value_type = value["type"]

        if value_type == 1:  # value is byte array
            value = base64.b64decode(value.get("bytes", ""))
        elif value_type == 2:  # value is uint64
            value = value.get("uint", 0)
        else:
            raise Exception(f"Unexpected state type: {value_type}")

        state[key] = value

    return state


## PYTEAL
def compile_teal_source(teal_source):
    """Compile teal and return teal binary code."""
    compile_response = _algod_client().compile(teal_source)
    return base64.b64decode(compile_response["result"])
