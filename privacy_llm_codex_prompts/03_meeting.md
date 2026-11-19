Read `AGENTS.md` and implement the meeting-intelligence workflow.

Add `/meeting`.

Behavior:
1. Create a genuinely fresh meeting session with no prior conversation context.
2. Enter `meeting_capture` mode.
3. Buffer multiple Telegram transcript messages.
4. Do NOT invoke the LLM per chunk.
5. Reset a 5-second inactivity timer after each transcript chunk.
6. After 5 seconds without another chunk, analyze the FULL accumulated transcript.
7. Send the result back to Telegram.
8. Persist transcript and meeting intelligence.

Do not require `/done`.
Do not persist or expose blank `/meeting` sessions.

Prompt files:
- `prompts/meeting_assistant.md`
- `prompts/ask.md`

Load them at runtime.

`meeting_assistant.md` should frame the model as an executive intelligence analyst, not a minutes generator.
Primary questions:
- What happened?
- Why does it matter?
- What positions are important stakeholders taking?
- What should leadership pay attention to next?

Output should be adaptive, narrative, contextual, and executive-level.
Surface decisions, implications, stakeholder alignment/conflict, risks, commitments, action items, unresolved issues, and likely next steps when relevant.

Do not force every meeting into a rigid template.

Transcript packaging must explicitly identify the content as a raw meeting transcript and mention interruptions, transcription errors, speaker changes, repetition, and filler.

`/ask <question>` must use the full stored transcript plus meeting context. Never answer from summary alone. Do not invent facts.

Run only lightweight tests for:
- multiple transcript chunks
- no premature analysis
- 5-second trigger
- `/ask`
- `/new`
