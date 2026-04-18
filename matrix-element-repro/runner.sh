#!/usr/bin/env bash
# Orchestrates the two-phase Element-vs-bot E2EE repro.
#
# phase1:  alice (matrix-js-sdk / rust-crypto) + bob (mautrix) create an E2EE
#          room; alice sends a message; expect bob to decrypt. PASS baseline.
#
# phase2:  bob rotates to a new device_id (simulating hermes-tee-staging ->
#          hermes-tee-staging-v2). Alice (same long-running session) sends
#          another message. If alice re-shares the megolm session to bob's
#          new device, PASS. If not, we've reproduced the staging bug in
#          matrix-js-sdk — the same crypto stack Element uses.
set -e

export BOOTSTRAP_TOKEN
export HOMESERVER=${HOMESERVER:-http://127.0.0.1:16167}

run_phase() {
    local phase=$1
    echo "=== PHASE: $phase ==="
    rm -f /app/bob_keys_ready
    # Alice registers first (her BOOTSTRAP_TOKEN unlocks the server for REG_TOKEN).
    # Bob starts 3s later so "repro-token" is already active when he registers.
    PHASE=$phase node /app/element_user.mjs &
    ALICE_PID=$!
    sleep 3
    PHASE=$phase /venv/bin/python /app/bot.py &
    BOT_PID=$!
    wait $ALICE_PID
    ALICE_EXIT=$?
    wait $BOT_PID
    BOT_EXIT=$?
    echo "=== phase $phase alice=$ALICE_EXIT bot=$BOT_EXIT ==="
    return $BOT_EXIT
}

run_phase phase1 && P1=0 || P1=$?
echo
echo "### baseline (phase1) exit=$P1"
echo
run_phase phase2 && P2=0 || P2=$?
echo
echo "### rotation (phase2) exit=$P2"
echo
echo "=================================================================="
if [ $P1 -eq 0 ] && [ $P2 -eq 0 ]; then
    echo "BOTH PHASES PASS — staging issue likely environment-specific."
elif [ $P1 -eq 0 ] && [ $P2 -ne 0 ]; then
    echo "PHASE1 PASS, PHASE2 FAIL — reproduces staging bug: matrix-js-sdk"
    echo "fails to re-share megolm session after bot device_id rotation."
else
    echo "PHASE1 FAIL — baseline broken, check setup."
fi
exit $P2
