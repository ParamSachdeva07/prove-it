"""A made-up bank account and the tools the agent can call.

Tools are plain async functions. Pipecat reads each one's name, docstring and
type hints to describe it to the model, and calls it directly. A risky tool
carries the @protected decorator, which checks its arguments and runs the
voice check before the tool's body can run. The model can only ask for a tool.
It can't skip the check, because the check wraps the function itself.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pipecat.adapters.llm.schemas.direct_function import tool_options

from .voice_key import CheckResult


@dataclass
class Bank:
    """All invented. Resets when the server restarts."""

    holder: str = "Param Sachdeva"
    balance: float = 7218.00
    payees: tuple[str, ...] = ("Sam Patel", "Priya Shah", "Alex Chen")
    payments: list[dict[str, Any]] = field(
        default_factory=lambda: [
            {"to": "Green Grocer", "amount": 18.40, "at": "yesterday"},
            {"to": "City Energy", "amount": 64.00, "at": "3 days ago"},
        ]
    )

    def find_payee(self, name: str) -> str | None:
        wanted = name.strip().lower()
        for payee in self.payees:
            if payee.lower() == wanted or payee.split()[0].lower() == wanted:
                return payee
        return None

    def snapshot(self) -> dict[str, Any]:
        return {
            "holder": self.holder,
            "balance": round(self.balance, 2),
            "payees": list(self.payees),
            "payments": self.payments[:6],
        }


class Guard(Protocol):
    async def verify(self, request: str) -> CheckResult: ...


Args = dict[str, Any]
Reply = Callable[[Any, dict[str, Any]], Awaitable[None]]

NOT_VERIFIED = {
    "status": "not_verified",
    # The model isn't told which check failed, so it can't coach an attacker.
    "message": "The caller couldn't be verified, so nothing was done. Don't retry unless they ask.",
}


def protected(
    guard: Guard,
    reply: Reply,
    check: Callable[[Args], str | None],
    describe: Callable[[Args], str],
):
    """Marks a tool as risky. Before the tool's body runs:

    1. `check` returns a question if the request can't go ahead as asked. It may
       also normalise the arguments in place, which the read-back and the tool then use.
    2. The guard runs the voice check, reading out `describe` to the caller.

    Only a passed check reaches the tool. Every failure gives the same answer.
    """

    def wrap(tool):
        @functools.wraps(tool)  # keeps the name, docstring and hints Pipecat reads
        async def checked(params, **args):
            question = check(args)
            if question:
                return await reply(params, {"status": "needs_clarification", "message": question})
            result = await guard.verify(describe(args))
            if not result.passed:
                return await reply(params, NOT_VERIFIED)
            return await tool(params, **args)

        return checked

    return wrap


def make_tools(bank: Bank, guard: Guard, notify=None) -> list[Callable]:
    """The tools for one call. They share the bank and this call's guard."""

    async def reply(params, outcome: dict[str, Any]) -> None:
        if notify:
            await notify(
                {"type": "tool", "stage": "finished", "name": params.function_name, "status": outcome["status"]}
            )
        await params.result_callback(outcome)

    async def get_balance(params):
        """The caller's current account balance in pounds."""
        await reply(params, {"status": "ok", "result": {"balance_pounds": round(bank.balance, 2)}})

    async def get_recent_payments(params):
        """The caller's most recent outgoing payments."""
        await reply(params, {"status": "ok", "result": {"payments": bank.payments[:5]}})

    def check_payment(args: Args) -> str | None:
        """Returns a question if the payment can't go ahead. Otherwise resolves the
        payee to its saved name and rounds the amount, in place, so the read-back
        and the payment both use exactly these values."""
        payee = bank.find_payee(str(args.get("payee", "")))
        if payee is None:
            return f"I can only pay saved payees: {', '.join(bank.payees)}. Who should I pay?"
        try:
            amount = round(float(args.get("amount_pounds", 0)), 2)
        except (TypeError, ValueError):
            amount = 0.0
        if amount <= 0:
            return "How much should I send?"
        if amount > bank.balance:
            return f"That's more than the balance of £{bank.balance:,.2f}. How much should I send?"
        args["payee"], args["amount_pounds"] = payee, amount
        return None

    def describe_payment(args: Args) -> str:
        return f"Send £{args['amount_pounds']:,.2f} to {args['payee']}"

    # Not cancelled when the caller speaks, and long enough for a security check.
    @tool_options(cancel_on_interruption=False, timeout_secs=120)
    @protected(guard, reply, check_payment, describe_payment)
    async def send_money(params, payee: str, amount_pounds: float):
        """Send money to one of the caller's saved payees. Runs a security check first.

        Args:
            payee: Name of a saved payee.
            amount_pounds: Amount in pounds, such as 40.
        """
        # check_payment has already resolved the payee and rounded the amount.
        bank.balance = round(bank.balance - amount_pounds, 2)
        bank.payments.insert(0, {"to": payee, "amount": amount_pounds, "at": time.strftime("%H:%M")})
        result = {"sent_pounds": amount_pounds, "to": payee, "new_balance_pounds": bank.balance}
        await reply(params, {"status": "ok", "result": result})

    return [get_balance, get_recent_payments, send_money]
