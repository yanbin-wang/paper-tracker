# Academic Paper Tracker

This project reads a CSTNET mailbox through **read-only IMAP**, identifies submission-related messages, stores private details locally, and publishes only a sanitized dataset to GitHub Pages.

The initial scan checks the latest 30 days of the inbox. Later runs process only newly arrived messages.

## Privacy model

- `private/tracker.sqlite3` contains the local index and is ignored by Git.
- The mailbox is opened with IMAP `EXAMINE`/read-only mode.
- Passwords are never written to the repository.
- `docs/data.json` contains only title, venue, public status, role, month, update date, and topic.
- Manuscript IDs, sender addresses, message bodies, decisions, and reviewer comments are never exported.

## Initial setup on macOS

1. Create a CSTNET **client-specific password** in the mail account security settings.
2. Copy the configuration:

   ```bash
   cp config.example.json config.json
   ```

3. Store the client-specific password in macOS Keychain (the command prompts without echoing it):

   ```bash
   security add-generic-password -a 'wangyanbin15@mails.ucas.ac.cn' \
     -s 'cstnet-paper-tracker' -w
   ```

4. Run the first private scan and public export:

   ```bash
   python3 tracker.py run
   ```

5. Preview the public site:

   ```bash
   python3 -m http.server 8000 -d docs
   ```

## GitHub Pages

Create a repository, commit this project, and configure Pages to deploy from the `main` branch `/docs` folder. Only the sanitized `docs/data.json` changes during scheduled updates.

## Automatic updates on this Mac

Replace `REPLACE_WITH_ABSOLUTE_PATH` in `com.yanbinwang.paper-tracker.plist.example`, copy it to `~/Library/LaunchAgents/com.yanbinwang.paper-tracker.plist`, then load it with:

```bash
launchctl load ~/Library/LaunchAgents/com.yanbinwang.paper-tracker.plist
```

The default interval is two hours. The Mac must be awake and connected to the internet.

## Review before publishing

The parser intentionally prefers false negatives over accidentally publishing unrelated mail. Review `docs/data.json` after the first scan. Corrections can be added as parser rules without exposing private messages.
