from collections import namedtuple
from datetime import datetime
import asyncio
from web3 import Web3
import json, math, os, sys, time

import slp_utils

RONIN_ADDRESS_PREFIX = "ronin:"
FEE_PAYOUT_PERCENTAGE = 0
FEE_PAYOUT_ADDRESS = Web3.toChecksumAddress("0xa0caa7803205026ec08818664c4211aff7565f56")

# Data types
Transaction = namedtuple("Transaction", "from_address to_address amount")
Payout = namedtuple("Payout", "name private_key nonce slp_balance scholar_transaction academy_transaction fee_transaction")
SlpClaim = namedtuple("SlpClaim", "name address private_key slp_claimed_balance slp_unclaimed_balance state")

def parseRoninAddress(address):
  assert(address.startswith(RONIN_ADDRESS_PREFIX))
  return Web3.toChecksumAddress(address.replace(RONIN_ADDRESS_PREFIX, "0x"))

def formatRoninAddress(address):
  return address.replace("0x", RONIN_ADDRESS_PREFIX)

def log(message="", end="\n"):
  print(message, end = end, flush=True)
  sys.stdout = log_file
  print(message, end = end) # print to log file
  sys.stdout = original_stdout # reset to original stdout
  log_file.flush()

def wait(seconds):
  for i in range(0, seconds):
    time.sleep(1)
    log(".", end="")
  log()

today = datetime.now()
log_path = f"logs/logs-{today.year}-{today.month:02}-{today.day:02}.txt"

if not os.path.exists(os.path.dirname(log_path)):
  os.makedirs(os.path.dirname(log_path))
log_file = open(log_path, "a", encoding="utf-8")
original_stdout = sys.stdout

log(f"*** Welcome to the SLP Payout program *** ({today})")

# Load accounts data.
if (len(sys.argv) != 2):
  log("Please specify the path to the json config file as parameter.")
  exit()

nonces = {}

with open(sys.argv[1]) as f:
  accounts = json.load(f)

academy_payout_address = parseRoninAddress(accounts["AcademyPayoutAddress"])

# Check for unclaimed SLP
log("Checking for unclaimed SLP")
slp_claims = []
new_line_needed = False
for scholar in accounts["Scholars"]:
  scholarName = scholar["Name"]
  account_address = parseRoninAddress(scholar["AccountAddress"])

  slp_unclaimed_balance = slp_utils.get_unclaimed_slp(account_address)

  nonce = nonces[account_address] = slp_utils.web3.eth.get_transaction_count(account_address)

  if (slp_unclaimed_balance > 0):
    if (new_line_needed):
      new_line_needed = False
      log()
    log(f"Account '{scholarName}' (nonce: {nonce}) has {slp_unclaimed_balance} unclaimed SLP.")
    
    slp_claims.append(SlpClaim(
      name = scholarName,
      address = account_address, 
      private_key = scholar["PrivateKey"],
      slp_claimed_balance = slp_utils.get_claimed_slp(account_address),
      slp_unclaimed_balance = slp_unclaimed_balance,
      state = { "signature": None }))
  else:
    log(f".", end="")
    new_line_needed = True

if (new_line_needed):
  new_line_needed = False
  log()

if (len(slp_claims) > 0):
  log("Would you like to claim SLP?", end=" ")

async def claim_slp(slp_claim, nonces):
  log(f"   Claiming {slp_claim.slp_unclaimed_balance} SLP for '{slp_claim.name}'...")
  claim_successful = await slp_utils.execute_slp_claim(slp_claim, nonces)
  if claim_successful:
      log(f"   SLP Claimed for account({slp_claim.address.replace('0x', 'ronin:')}!")
  else:
      log(f"   SLP Claim for account ({slp_claim.address.replace('0x', 'ronin:')}) failed")
  return {
    "slp_claim": slp_claim,
    "is_successful": claim_successful
  }

while (len(slp_claims) > 0):
  if (input() == "y"):
    loop = asyncio.get_event_loop()
    results = loop.run_until_complete(asyncio.gather(*[claim_slp(slp_claim, nonces) for slp_claim in slp_claims]))
    log("DONE")

    failed_claims = [result["slp_claim"] for result in results if result["is_successful"] == False]
    if (len(failed_claims) > 0):
      log("The following claims didn't complete successfully:")
      for slp_claim in failed_claims:
        log(f"  - Account '{slp_claim.name}' has {slp_claim.slp_unclaimed_balance} unclaimed SLP.")
      slp_claims = failed_claims.copy()
      log("Would you like to retry claim process? ", end="")
    else:
      log("All claims completed successfully!")
      slp_claims = []
  else:
    break

log()
log("Please review the payouts for each scholar:")

# Generate transactions.
payouts = []

for scholar in accounts["Scholars"]:
  scholarName = scholar["Name"]
  account_address = parseRoninAddress(scholar["AccountAddress"])
  scholar_payout_address = parseRoninAddress(scholar["ScholarPayoutAddress"])

  slp_balance = slp_utils.get_claimed_slp(account_address)

  if (slp_balance == 0):
    log(f"Skipping account '{scholarName}' ({formatRoninAddress(account_address)}) because SLP balance is zero.")
    continue
  
  scholar_payout_percentage = scholar["ScholarPayoutPercentage"]
  assert(scholar_payout_percentage >= 0 and scholar_payout_percentage <= 1)

  fee_payout_amount = math.floor(slp_balance * FEE_PAYOUT_PERCENTAGE)
  slp_balance_minus_fees = slp_balance - fee_payout_amount
  scholar_payout_amount = math.ceil(slp_balance_minus_fees * scholar_payout_percentage)
  academy_payout_amount = slp_balance_minus_fees - scholar_payout_amount
  
  assert(scholar_payout_amount >= 0)
  assert(academy_payout_amount >= 0)
  assert(slp_balance == scholar_payout_amount + academy_payout_amount + fee_payout_amount)
  
  payouts.append(Payout(
    name = scholarName,
    private_key = scholar["PrivateKey"],
    slp_balance = slp_balance,
    nonce = nonces[account_address],
    scholar_transaction = Transaction(from_address = account_address, to_address = scholar_payout_address, amount = scholar_payout_amount),
    academy_transaction = Transaction(from_address = account_address, to_address = academy_payout_address, amount = academy_payout_amount),
    fee_transaction = Transaction(from_address = account_address, to_address = FEE_PAYOUT_ADDRESS, amount = fee_payout_amount)))

log()

if (len(payouts) == 0):
  exit()

# Preview transactions.
for payout in payouts:
  log(f"Payout for '{payout.name}'")
  log(f"├─ SLP balance: {payout.slp_balance} SLP")
  log(f"├─ Nonce: {payout.nonce}")
  log(f"├─ Scholar payout: send {payout.scholar_transaction.amount:5} SLP from {formatRoninAddress(payout.scholar_transaction.from_address)} to {formatRoninAddress(payout.scholar_transaction.to_address)}")
  log(f"├─ Academy payout: send {payout.academy_transaction.amount:5} SLP from {formatRoninAddress(payout.academy_transaction.from_address)} to {formatRoninAddress(payout.academy_transaction.to_address)}")
  log(f"└─ Fee           : send {payout.fee_transaction.amount:5} SLP from {formatRoninAddress(payout.fee_transaction.from_address)} to {formatRoninAddress(payout.fee_transaction.to_address)}")
  log()

log("Would you like to execute transactions (y/n) ?", end=" ")
if (input() != "y"):
  log("No transaction was executed. Program will now stop.")
  exit()

# Execute transactions.
log()
log("Executing transactions...")

async def execute_payout(payout):
  pendingTransactions = []
  log(f"Executing payout for '{payout.name}'")
  if (payout.scholar_transaction.amount > 0):
    log(f"├─ Scholar payout: sending {payout.scholar_transaction.amount} SLP from {formatRoninAddress(payout.scholar_transaction.from_address)} to {formatRoninAddress(payout.scholar_transaction.to_address)}...", end="")
    hash = slp_utils.transfer_slp(payout.scholar_transaction, payout.private_key, payout.nonce)
    time.sleep(0.250)
    log(f"│  Hash: {hash} - Explorer: https://explorer.roninchain.com/tx/{str(hash)}")
    pendingTransactions.append({ "name": payout.name, "hash": hash, "action": "Scholar payout" })
  else:
    payout.nonce - 1
    log(f"├─ Skipping Scholar payout: amount is 0 SLP")

  if (payout.academy_transaction.amount > 0):
    log(f"├─ Academy payout: sending {payout.academy_transaction.amount} SLP from {formatRoninAddress(payout.academy_transaction.from_address)} to {formatRoninAddress(payout.academy_transaction.to_address)}...", end="")
    hash = slp_utils.transfer_slp(payout.academy_transaction, payout.private_key, payout.nonce + 1)
    time.sleep(0.250)
    log(f"│  Hash: {hash} - Explorer: https://explorer.roninchain.com/tx/{str(hash)}")
    pendingTransactions.append({ "name": payout.name, "hash": hash, "action": "Academy payout" })
  else:
    payout.nonce - 1
    log(f"├─ Skipping Academy payout: amount is 0 SLP")

  if (payout.fee_transaction.amount > 0):
    log(f"└─ Fee payout: sending {payout.fee_transaction.amount} SLP from {formatRoninAddress(payout.fee_transaction.from_address)} to {formatRoninAddress(payout.fee_transaction.to_address)}...", end="")
    hash = slp_utils.transfer_slp(payout.fee_transaction, payout.private_key, payout.nonce + 2)
    time.sleep(0.250)
    log(f"   Hash: {hash} - Explorer: https://explorer.roninchain.com/tx/{str(hash)}")
    pendingTransactions.append({ "name": payout.name, "hash": hash, "action": "Fee payout" })
  else:
    log(f"├─ Skipping Fee payout: amount is 0 SLP")
  loop = asyncio.get_event_loop()
  loop.run_until_complete(asyncio.gather(*[slp_utils.wait_for_transaction_to_complete(tran) for tran in pendingTransactions]))

# for payout in payouts:
#   execute_payout(payout)
loop = asyncio.get_event_loop()
loop.run_until_complete(asyncio.gather(*[execute_payout(payout) for payout in payouts]))
log()