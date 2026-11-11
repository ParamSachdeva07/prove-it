"""The tool layer: protected tools only run after a passed check."""

from app.bank import NOT_VERIFIED, Bank, make_tools
from app.voice_key import CheckResult


class FakeGuard:
    def __init__(self, passes: bool):
        self.passes = passes
        self.requests: list[str] = []

    async def verify(self, request: str) -> CheckResult:
        self.requests.append(request)
        reason = [] if self.passes else ["voice doesn't match"]
        return CheckResult(failed=reason, speaker=0.8 if self.passes else 0.1, spoof=0.01, code_ok=True)


class FakeParams:
    """Stands in for Pipecat's FunctionCallParams: records the tool's answer."""

    def __init__(self, name: str):
        self.function_name = name
        self.results: list[dict] = []

    async def result_callback(self, result):
        self.results.append(result)


def tools(bank, guard):
    return {fn.__name__: fn for fn in make_tools(bank, guard)}


async def call(bank, guard, name, **args):
    params = FakeParams(name)
    await tools(bank, guard)[name](params=params, **args)
    return params.results[0]


async def test_open_tools_never_run_the_check():
    bank, guard = Bank(), FakeGuard(passes=False)
    assert await call(bank, guard, "get_balance") == {"status": "ok", "result": {"balance_pounds": 7218.0}}
    assert guard.requests == []


async def test_a_failed_check_changes_nothing():
    bank, guard = Bank(), FakeGuard(passes=False)
    outcome = await call(bank, guard, "send_money", payee="Sam", amount_pounds=40)
    assert outcome == NOT_VERIFIED
    assert bank.balance == 7218.0
    # The caller hears the payee the money would really go to, not the model's wording.
    assert guard.requests == ["Send £40.00 to Sam Patel"]


async def test_a_passed_check_runs_the_tool_once():
    bank, guard = Bank(), FakeGuard(passes=True)
    outcome = await call(bank, guard, "send_money", payee="sam", amount_pounds=40)
    assert outcome["status"] == "ok"
    assert outcome["result"] == {"sent_pounds": 40.0, "to": "Sam Patel", "new_balance_pounds": 7178.0}
    assert bank.payments[0]["to"] == "Sam Patel"


async def test_bad_arguments_ask_a_question_before_any_check():
    bank, guard = Bank(), FakeGuard(passes=True)
    unknown = await call(bank, guard, "send_money", payee="Mallory", amount_pounds=40)
    too_much = await call(bank, guard, "send_money", payee="Sam", amount_pounds=999999)
    assert unknown["status"] == too_much["status"] == "needs_clarification"
    assert guard.requests == []
    assert bank.balance == 7218.0


async def test_the_model_is_not_told_which_check_failed():
    text = str(NOT_VERIFIED).lower()
    for detail in ("voice", "synthetic", "code", "spoof", "speaker"):
        assert detail not in text


async def test_risky_tools_keep_their_call_options():
    send_money = tools(Bank(), FakeGuard(passes=True))["send_money"]
    assert send_money._pipecat_cancel_on_interruption is False
    assert send_money._pipecat_timeout_secs == 120
