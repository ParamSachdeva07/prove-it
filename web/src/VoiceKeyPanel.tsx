import { useEffect, useRef, useState } from "react";
import type { Status } from "./App";
import { record } from "./recorder";

type ClipResult = { seconds: number; spoof: number; problem: string | null };
type EnrolResult = { ok: boolean; clips: ClipResult[] };

export function VoiceKeyPanel({ status, onChange }: { status: Status | null; onChange: () => void }) {
  const [sentences, setSentences] = useState<string[]>([]);
  const [clips, setClips] = useState<(Blob | null)[]>([null, null, null]);
  const [recording, setRecording] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<EnrolResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [replacing, setReplacing] = useState(false);
  const recorder = useRef<(() => Promise<Blob>) | null>(null);

  useEffect(() => {
    fetch("/api/voice-key")
      .then((response) => response.json())
      .then((data) => setSentences(data.sentences))
      .catch(() => setError("Can't reach the server."));
  }, []);

  const startRecording = async (index: number) => {
    setError(null);
    setResult(null);
    try {
      recorder.current = await record();
      setRecording(index);
    } catch {
      setError("The microphone couldn't start. Check the browser's permission.");
    }
  };

  const stopRecording = async () => {
    if (recording === null || !recorder.current) return;
    const blob = await recorder.current();
    setClips((current) => current.map((clip, i) => (i === recording ? blob : clip)));
    setRecording(null);
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    const form = new FormData();
    clips.forEach((clip, i) => clip && form.append("clips", clip, `sentence-${i + 1}.wav`));
    try {
      const response = await fetch("/api/voice-key", { method: "POST", body: form });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? "Saving failed.");
      setResult(data);
      if (data.ok) {
        setReplacing(false);
        setClips([null, null, null]);
        onChange();
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Saving failed.");
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    await fetch("/api/voice-key", { method: "DELETE" });
    setResult(null);
    onChange();
  };

  const hasKey = Boolean(status?.has_voice_key);
  const ready = Boolean(status?.models_ready);

  return (
    <section className="panel">
      <h2>1. Voice key</h2>
      {hasKey && !replacing ? (
        <>
          <p>
            Your voice key is saved. Sending money will check your live voice against it.
          </p>
          {result?.ok && <p className="good">Saved. Each sentence passed the checks.</p>}
          <div className="row">
            <button onClick={() => setReplacing(true)}>Record a new key</button>
            <button className="quiet" onClick={() => void remove()}>
              Delete key
            </button>
          </div>
        </>
      ) : (
        <>
          <p>
            Read each sentence aloud in your normal voice, in a quiet room, with the mic you'll call
            from. Each one becomes part of your voiceprint.
          </p>
          <ol className="sentences">
            {sentences.map((sentence, i) => {
              const clipResult = result?.clips[i];
              const isRecording = recording === i;
              return (
                <li key={i} className={isRecording ? "is-recording" : ""}>
                  <p className="sentence">{sentence}</p>
                  <div className="row">
                    {isRecording ? (
                      <button className="danger" onClick={() => void stopRecording()}>
                        Stop
                      </button>
                    ) : (
                      <button disabled={recording !== null || saving} onClick={() => void startRecording(i)}>
                        {clips[i] ? "Record again" : "Record"}
                      </button>
                    )}
                    {isRecording && (
                      <span className="muted">Recording…</span>
                    )}
                    {!isRecording && clips[i] && !clipResult && <span className="muted">Recorded</span>}
                    {clipResult && (
                      <span className={clipResult.problem ? "bad" : "good"}>
                        {clipResult.problem
                          ? `Problem: ${clipResult.problem} (${clipResult.seconds}s, synthetic ${clipResult.spoof.toFixed(2)})`
                          : `OK (${clipResult.seconds}s, synthetic ${clipResult.spoof.toFixed(2)})`}
                      </span>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
          <div className="row">
            <button
              className="primary"
              disabled={!ready || saving || recording !== null || clips.some((clip) => !clip)}
              onClick={() => void save()}
            >
              {saving ? "Checking…" : "Save voice key"}
            </button>
            {replacing && (
              <button className="quiet" onClick={() => setReplacing(false)}>
                Cancel
              </button>
            )}
            {!ready && <span className="muted">Waiting for the voice models…</span>}
          </div>
          {result && !result.ok && <p className="bad">Re-record the sentences marked with a problem, then save again.</p>}
        </>
      )}
      {error && <p className="bad">{error}</p>}
    </section>
  );
}
