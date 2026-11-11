# How AI was used

## Tools

| Tool | Used for |
|---|---|
| Claude Code (Claude Opus 5.5) | Writing most of the code to my design, tests, debugging, and documentation |
| GPT-Realtime | Part of the product: the conversational agent |
| Cartesia Sonic | Part of the product: the security voice that reads the code |

## How I used Claude

- **Implementation.** Claude wrote most of the code to my spec, using Pipecat, FastAPI, React, SpeechBrain, DF Arena and faster-whisper. I reviewed it and asked for changes.
- **Explanation.** I had Claude walk me through every file, Pipecat's pipeline model and how WebRTC connects, so I could explain and change the code myself.
- **Suggestions I reviewed and accepted:**
  - **Reading the request back to the caller** before the code ("You asked to send £40.00 to Sam Patel"), with the payee resolved from the real arguments, so a caller can't approve something they didn't hear.
  - **Splitting the audio code in two:** a small router that only decides whether the model hears the caller, and a separate recorder.

## What I designed

- **The idea:** step-up voice authentication inside a voice agent's tool calls. Voice agents are starting to move money and change accounts, but a few seconds of audio is enough to clone a voice, and an LLM can be talked into almost anything. So the agent should chat freely, but a sensitive action should need proof that the real account holder is speaking, live, at that moment.

- **The architecture:**
  1. **A basic voice agent handles everyday questions.** GPT-Realtime holds the conversation, and open tools like checking the balance run straight away.
  2. **Sensitive tools run a security check inside the tool call itself.** The model can only *ask* for a tool. The check is code that runs before the tool's body, so no prompt or persuasion can skip it.
  3. **The model is cut off during the check.** The caller's audio is held back from the model and recorded by the server. A separate security voice reads the challenge, so the model never sees the code or hears the answer.
  4. **Only then does the tool run.** The model is told `ok` or `not_verified`, never which check failed, so it can't coach an attacker.
  5. **One server sits in the middle of the audio.** It doesn't just receive tool calls, so it controls what the model hears.

- **The three-part check,** where every part must pass:
  - **Voice key:** the live voice must match the voiceprint the user enrolled. This stops someone else calling as you.
  - **Anti-spoofing:** the speech must not be synthetic. This stops a cloned voice.
  - **Live challenge:** the caller must say a fresh random code. This stops replaying an old recording.

  Each check alone can be beaten. Together they cover replay, cloning and a live impostor.

- **The features:**
  - **Self-service voice keys, for the demo:** anyone trying the prototype can set their own key in the browser by reading three sentences, then try to fool it. A real deployment would require sign-in and approval on a second device before a key could be set or changed.
  - **Open versus protected tools:** checking the balance and recent payments need no check, and sending money does.
  - **A live view:** a page showing the conversation, each stage of the check and its scores, and the account updating when a payment goes through.

- **The `@protected` decorator,** which wraps a risky tool so the check runs before its body can. Securing a new tool takes one line: write the tool as a normal function and add `@protected`. The tool code never repeats the security logic, and there's no separate list of protected tools to keep in step. Before the tool runs, the decorator:
  1. checks the arguments, and asks the caller a question if they're wrong
  2. runs the security check
  3. returns the same "not verified" answer for every failure.

Some of these decisions came from cutting back:
- **Simplifying.** I removed overengineering and simplified the code's logic, keeping only the essential workflow, so every part is easy to read, explain and change.
- **Testing the security claims.** For example, I asked what sending the caller's answer to a third-party deepfake detector would cost in privacy and reliability, and whether OpenAI could call a deterministic security tool directly.

## Verification

Generated code was tested, not trusted:
- **Fast tests with stand-ins,** for the security properties: the model is cut off while the answer is recorded, audio always comes back, a new code every time, and the model never learns which check failed.
- **Slow tests on the real models,** using macOS voices: the same voice matches, a different voice doesn't, a wrong code fails, and synthetic speech is caught.
- **Real calls,** against the real OpenAI and Cartesia services.

These caught real bugs:

- **The first real call failed with `CERTIFICATE_VERIFY_FAILED`.** The python.org build of Python on macOS ships without trusted certificates. `truststore` fixed it.
- **`TTSSpeakFrame(interruptible=False)` isn't a valid argument in Pipecat 1.15.0.** The flag is now set after the frame is created.
- **A speech service placed after the model would repeat its replies,** so the security voice sits before it in the pipeline.
- **Pipecat doesn't count a plain audio frame as bot speech,** so waiting for the beep timed out. The guard now waits for the beep's known length.
- **After switching to Pipecat's browser SDK, the agent was silent.** The page received its audio but never played it. The page now attaches the track to an audio element.
