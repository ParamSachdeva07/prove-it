import { useEffect, useRef, useState } from "react";
import type { Status } from "./App";
import { PipecatClient, TransportState } from "@pipecat-ai/client-js";
import { SmallWebRTCTransport } from "@pipecat-ai/small-webrtc-transport";

type CallState = "idle" | "connecting" | "connected" | "ended" | "failed";

/** Pipecat's ten connection states, reduced to the five the page shows. */
const STATES: Partial<Record<TransportState, CallState>> = {
  connecting: "connecting",
  ready: "connected",
  disconnected: "ended",
  error: "failed",
};

type Line = { who: "You" | "Agent" | "Security voice" | "System"; text: string };

/** The agent's speech arrives a word at a time. No space before punctuation or "'t". */
function joinWords(before: string, next: string): string {
  return /^[.,!?;:%)’']/.test(next) ? `${before}${next}` : `${before} ${next}`;
}

type Security = {
  stage: "prompt" | "listening" | "checking" | "result";
  request?: string;
  text?: string;
  code?: string;
  passed?: boolean;
  speaker?: number;
  spoof?: number;
  heard?: string;
  // Pass or fail for each check, decided by the server (voice_key.py).
  speaker_ok?: boolean;
  spoof_ok?: boolean;
  code_ok?: boolean;
  failed?: string[];
  limits?: { speaker_min: number; spoof_max: number };
};

const STAGE_TEXT: Record<Security["stage"], string> = {
  prompt: "The security voice is reading the code.",
  listening: "Listening. Say the phrase after the beep.",
  checking: "Checking your answer against your voice key.",
  result: "",
};

export function CallPanel({ status }: { status: Status | null }) {
  const [state, setState] = useState<CallState>("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const [security, setSecurity] = useState<Security | null>(null);
  const [error, setError] = useState<string | null>(null);
  const call = useRef<PipecatClient | null>(null);
  const transcript = useRef<HTMLOListElement | null>(null);
  // Plays the agent's voice. The Pipecat client receives the audio track but doesn't play it.
  const speaker = useRef<HTMLAudioElement | null>(null);
  // While a check runs, speech from the call is the security voice, not the model.
  const checking = useRef(false);

  useEffect(() => {
    transcript.current?.scrollTo({ top: transcript.current.scrollHeight });
  }, [lines]);

  useEffect(() => () => void call.current?.disconnect(), []);

  const add = (who: Line["who"], text: string) =>
    setLines((current) => {
      const last = current[current.length - 1];
      // Speech arrives in pieces. Keep one line per turn.
      if ((who === "Agent" || who === "Security voice") && last?.who === who) {
        return [...current.slice(0, -1), { who, text: joinWords(last.text, text) }];
      }
      return [...current, { who, text }];
    });

  const start = async () => {
    setLines([]);
    setSecurity(null);
    setError(null);
    setState("connecting");
    const client = new PipecatClient({
      transport: new SmallWebRTCTransport({ iceServers: [] }),
      enableMic: true,
      enableCam: false,
      callbacks: {
        onTrackStarted: (track, participant) => {
          if (track.kind !== "audio" || participant?.local || !speaker.current) return;
          speaker.current.srcObject = new MediaStream([track]);
          void speaker.current.play().catch(() => undefined);
        },
        onTrackStopped: (track, participant) => {
          if (track.kind === "audio" && !participant?.local && speaker.current) speaker.current.srcObject = null;
        },
        onTransportStateChanged: (next) => {
          const mapped = STATES[next];
          if (mapped) setState(mapped);
        },
        onError: (message) =>
          setError((message.data as { error?: string })?.error ?? "The agent reported an error."),
        onUserTranscript: (data) => {
          if (data.final && data.text.trim()) add("You", data.text.trim());
        },
        onBotOutput: (data) => {
          // Each piece arrives as "new", then as progress updates; keep the first.
          const isNew = data.spoken_status === undefined || data.spoken_status === "new";
          if (isNew && data.spoken !== false && data.text.trim())
            add(checking.current ? "Security voice" : "Agent", data.text.trim());
        },
        onServerMessage: (data: Record<string, unknown>) => {
          if (data.type === "security") checking.current = data.stage === "prompt" || data.stage === "listening";
          if (data.type === "security" && data.stage !== "done") setSecurity(data as unknown as Security);
          if (data.type === "tool" && data.stage === "called") add("System", `Tool requested: ${String(data.name)}`);
          if (data.type === "tool" && data.stage === "finished")
            add("System", `${String(data.name)} → ${String(data.status)}`);
        },
      },
    });
    call.current = client;
    try {
      await client.connect({ webrtcRequestParams: { endpoint: "/api/offer" } });
    } catch (err) {
      setError(err instanceof Error ? err.message : "The call failed.");
      setState("failed");
      void client.disconnect();
    }
  };

  const stop = () => {
    void call.current?.disconnect();
    call.current = null;
    setState("ended");
  };

  const live = state === "connecting" || state === "connected";
  const canStart = Boolean(status?.can_call && status?.models_ready);

  return (
    <section className="panel call">
      <h2>2. Call the bank</h2>
      <div className="row">
        {live ? (
          <button className="danger" onClick={stop}>
            End call
          </button>
        ) : (
          <button className="primary" disabled={!canStart} onClick={() => void start()}>
            Start call
          </button>
        )}
        <span className="muted">
          {state === "connecting" && "Connecting…"}
          {state === "connected" && "On the call. Try “What's my balance?” or “Send Sam forty pounds.”"}
          {state === "idle" && !status?.can_call && "Add your API keys to .env to make calls."}
          {state === "idle" && status?.can_call && !status.has_voice_key && "Set a voice key first, or payments will be refused."}
          {state === "failed" && "The call failed."}
          {state === "ended" && "Call ended."}
        </span>
      </div>
      {error && <p className="bad">{error}</p>}

      <ol className="transcript" ref={transcript} aria-live="polite">
        {lines.length === 0 && <li className="muted">The conversation appears here.</li>}
        {lines.map((line, i) => (
          <li key={i} className={`line line--${line.who === "Security voice" ? "security" : line.who.toLowerCase()}`}>
            <strong>{line.who}</strong> {line.text}
          </li>
        ))}
      </ol>

      <SecurityCard security={security} />
      <audio ref={speaker} autoPlay />
    </section>
  );
}

function SecurityCard({ security }: { security: Security | null }) {
  if (!security) {
    return (
      <div className="security">
        <h3>Security check</h3>
        <p className="muted">Runs inside protected tool calls, such as sending money.</p>
      </div>
    );
  }
  const done = security.stage === "result";
  const limits = security.limits;
  return (
    <div className={`security ${done ? (security.passed ? "security--pass" : "security--fail") : "security--live"}`}>
      <h3>Security check{security.request ? `: ${security.request}` : ""}</h3>
      {!done && (
        <>
          <p>{STAGE_TEXT[security.stage]}</p>
          {security.text && <p className="prompt">“{security.text}”</p>}
        </>
      )}
      {done && (
        <>
          {limits && (
            <table className="checks">
              <tbody>
                <Check
                  label="Voice matches key"
                  ok={Boolean(security.speaker_ok)}
                  value={`${(security.speaker ?? 0).toFixed(2)} (needs ≥ ${limits.speaker_min.toFixed(2)})`}
                />
                <Check
                  label="Synthetic speech"
                  ok={Boolean(security.spoof_ok)}
                  value={`${(security.spoof ?? 0).toFixed(2)} (needs < ${limits.spoof_max.toFixed(2)})`}
                />
                <Check
                  label={`Said code ${security.code ?? ""}`}
                  ok={Boolean(security.code_ok)}
                  value={security.heard ? `heard “${security.heard}”` : "nothing heard"}
                />
              </tbody>
            </table>
          )}
          <p className={security.passed ? "good" : "bad"}>
            {security.passed
              ? "Passed. The tool ran."
              : `Not verified, so nothing was done${security.failed?.length ? `: ${security.failed.join(", ")}` : ""}.`}
          </p>
        </>
      )}
    </div>
  );
}

function Check({ label, ok, value }: { label: string; ok: boolean; value: string }) {
  return (
    <tr>
      <th scope="row">{label}</th>
      <td>{value}</td>
      <td className={ok ? "good" : "bad"}>{ok ? "Pass" : "Fail"}</td>
    </tr>
  );
}
