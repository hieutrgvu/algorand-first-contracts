import base64

from algosdk.future import transaction
from algosdk import account, mnemonic
from algosdk.v2client import algod
from pyteal import *

creator_mnemoic = "YOUR MNEMONIC"
algod_address = "http://localhost:4001"
algod_token = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

def approval_program():
    on_creation = Seq([
        App.globalPut(Bytes("Count"), Int(0)),
        Return(Int(1))
    ])

    scratchCount = ScratchVar(TealType.uint64)

    add = Seq([
        scratchCount.store(App.globalGet(Bytes("Count"))),
        App.globalPut(Bytes("Count"), scratchCount.load() + Int(1)),
        Return(Int(1))
    ])

    deduct = Seq([
        scratchCount.store(App.globalGet(Bytes("Count"))),
        If(scratchCount.load() > Int(0),
            App.globalPut(Bytes("Count"), scratchCount.load() - Int(1)),
        ),
        Return(Int(1))
    ])

    handle_noop = Cond(
        [And(
            Global.group_size() == Int(1),
            Txn.application_args[0] == Bytes("Add")
        ), add],
        [And(
            Global.group_size() == Int(1),
            Txn.application_args[0] == Bytes("Deduct")
        ), deduct],
    )

    program = Cond(
        [Txn.application_id() == Int(0), on_creation],
        [Txn.on_completion() == OnComplete.OptIn, Return(Int(0))],
        [Txn.on_completion() == OnComplete.CloseOut, Return(Int(0))],
        [Txn.on_completion() == OnComplete.UpdateApplication, Return(Int(0))],
        [Txn.on_completion() == OnComplete.DeleteApplication, Return(Int(0))],
        [Txn.on_completion() == OnComplete.NoOp, handle_noop]
    )

    return compileTeal(program, Mode.Application, version=5)

def clear_state_program():
    program = Return(Int(1))
    return compileTeal(program, Mode.Application, version=5)

def compile_program(client, source_code):
    compile_response = client.compile(source_code)
    return base64.b64decode(compile_response['result'])

def wait_for_confirmation(client, transaction_id, timeout):
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
            raise Exception('pool error: {}'.format(pending_txn["pool-error"]))
        client.status_after_block(current_round)
        current_round += 1
    raise Exception("pending tx not found in timeout rounds, timeout value = : {}".format(timeout))

def format_state(state):
    formatted = {}
    for item in state:
        key = item['key']
        value = item['value']
        formatted_key = base64.b64decode(key).decode('utf-8')
        if value['type'] == 1:
            if formatted_key == 'voted':
                formatted_value = base64.b64decode(value['bytes']).decode('utf-8')
            else:
                formatted_value = value['bytes']
            formatted[formatted_key] = formatted_value
        else:
            formatted[formatted_key] = value['uint']
    return formatted

def read_global_state(client, addr, app_id):
    results = client.account_info(addr)
    apps_created = results['created-apps']
    for app in apps_created:
        if app['id'] == app_id:
            return format_state(app['params']['global-state'])
    return {}

def create_app(client, private_key, approval_program, clear_program, global_schema, local_schema):
    sender = account.address_from_private_key(private_key)
    on_complete = transaction.OnComplete.NoOpOC.real
    params = client.suggested_params()
    txn = transaction.ApplicationCreateTxn(sender, params, on_complete, approval_program, clear_program, global_schema, local_schema)
    signed_txn = txn.sign(private_key)
    tx_id = signed_txn.transaction.get_txid()
    client.send_transactions([signed_txn])
    wait_for_confirmation(client, tx_id, 5)
    transaction_response = client.pending_transaction_info(tx_id)
    app_id = transaction_response['application-index']
    print("Created new app_id:", app_id)
    return app_id

def call_app(client, private_key, index, app_args):
    sender = account.address_from_private_key(private_key)
    params = client.suggested_params()
    txn = transaction.ApplicationNoOpTxn(sender, params, index, app_args)
    signed_txn = txn.sign(private_key)
    tx_id = signed_txn.transaction.get_txid()
    client.send_transactions([signed_txn])
    wait_for_confirmation(client, tx_id, 5)
    print("Application called")

def main():
    algod_client = algod.AlgodClient(algod_token, algod_address)
    creator_private_key = mnemonic.to_private_key(creator_mnemoic)
    global_schema = transaction.StateSchema(num_uints=1, num_byte_slices=0)
    local_schema = transaction.StateSchema(num_uints=0, num_byte_slices=0)

    approval_program_compiled = compile_program(algod_client, approval_program())
    clear_state_program_compiled = compile_program(algod_client, clear_state_program())

    print("--------------------------------------------")
    print("Deploying Counter application...")
    app_id = create_app(algod_client, creator_private_key, approval_program_compiled, clear_state_program_compiled, global_schema, local_schema)
    print("Global state:", read_global_state(algod_client, account.address_from_private_key(creator_private_key), app_id))

    print("--------------------------------------------")
    print("Calling Counter application...")
    call_app(algod_client, creator_private_key, app_id, app_args=["Add"])
    print("Global state:", read_global_state(algod_client, account.address_from_private_key(creator_private_key), app_id))

main()