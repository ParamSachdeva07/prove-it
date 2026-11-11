import { useCallback, useEffect, useState } from "react";
import { CallPanel } from "./CallPanel";
import { VoiceKeyPanel } from "./VoiceKeyPanel";

export type Status = {
  models_ready: boolean;
  models_error: string | null;
  can_call: boolean;
  has_voice_key: boolean;
  limits: { speaker_min: number; spoof_max: number };
};

type Account = {
  holder: string;
  balance: number;
  payees: string[];
  payments: { to: string; amount: number; at: string }[];
};

/** Fetches a URL now and every `ms` milliseconds. */
function usePoll<T>(url: string, ms: number): [T | null, () => void] {
  const [data, setData] = useState<T | null>(null);
  const load = useCallback(() => {
    fetch(url)
      .then((response) => (response.ok ? response.json() : null))
      .then((value) => value && setData(value))
      .catch(() => undefined);
  }, [url]);
  useEffect(() => {
    load();
    const timer = setInterval(load, ms);
    return () => clearInterval(timer);
  }, [load, ms]);
  return [data, load];
}

const pounds = (value: number) =>
  `£${value.toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export function App() {
  const [status, refreshStatus] = usePoll<Status>("/api/status", 2000);
  const [account] = usePoll<Account>("/api/account", 1500);

  return (
    <div className="page">
      <header className="header">
        <div>
          <h1>Prove It</h1>
          <p className="lede">
            A voice agent whose sensitive tools check your voice before they run. Ask about your
            balance freely. Sending money needs your voice key, a live code and a real voice.
          </p>
        </div>
      </header>
      {status?.models_error && <p className="alert">The voice models failed to load: {status.models_error}</p>}

      <main className="grid">
        <VoiceKeyPanel status={status} onChange={refreshStatus} />
        <CallPanel status={status} />
        <section className="panel account">
          <h2>Account</h2>
          {account ? (
            <>
              <p className="muted">{account.holder}</p>
              <p className="balance">{pounds(account.balance)}</p>
              <h3>Recent payments</h3>
              <ul className="payments">
                {account.payments.map((payment, i) => (
                  <li key={`${payment.to}-${i}`}>
                    <span>{payment.to}</span>
                    <span className="muted">{payment.at}</span>
                    <span className="amount">−{pounds(payment.amount)}</span>
                  </li>
                ))}
              </ul>
              <p className="muted small">Saved payees: {account.payees.join(", ")}</p>
            </>
          ) : (
            <p className="muted">Waiting for the server…</p>
          )}
        </section>
      </main>
    </div>
  );
}
