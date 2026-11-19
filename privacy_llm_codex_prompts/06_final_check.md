Read `AGENTS.md` and perform a lightweight final integration review.

Do not redesign anything. Fix only clear defects.

Check:
1. Web chat works.
2. Settings work.
3. OpenRouter connection status is shown without exposing the API key.
4. OpenRouter model selection works.
5. OpenRouter model search/discovery works when configured.
6. Model metadata/details render safely when available.
7. Selected model persists and is used by subsequent requests.
8. OpenRouter connection/model test works and fails gracefully.
9. Privacy tokenization/rehydration work.
10. Telegram authorization works.
11. Telegram normal chat works.
12. `/new` does not create blank persisted sessions.
13. `/meeting` starts a fresh session.
14. Transcript chunks buffer correctly.
15. Meeting analysis occurs only after 5 seconds of inactivity.
16. Meeting prompt loads from `prompts/meeting_assistant.md`.
17. `/ask` uses the full transcript.
18. Telegram-created sessions appear in Web.
19. Web can continue Telegram-created sessions.
20. Mobile-first Web UI still works.

Do not build a large test suite.

Report:
- what was checked
- defects fixed
- anything incomplete
- exact startup command
