/**
 * Records the mic as a WAV file, for the voice key.
 * WAV because the server can read it with no extra tools.
 */
import { MediaRecorder, register } from "extendable-media-recorder";
import { connect } from "extendable-media-recorder-wav-encoder";

// Teach MediaRecorder to write WAV. The library allows this once only.
const wavReady = connect().then(register);

/** Starts recording. Call the returned function to stop and get the WAV. */
export async function record(): Promise<() => Promise<Blob>> {
  await wavReady;
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } });
  const recorder = new MediaRecorder(stream, { mimeType: "audio/wav" });
  recorder.start();
  return () =>
    new Promise((resolve) => {
      recorder.ondataavailable = (event) => resolve(event.data);
      recorder.stop();
      stream.getTracks().forEach((track) => track.stop());
    });
}
