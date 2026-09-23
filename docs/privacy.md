# Privacy Policy

Last updated: September 2026

InboxPilot is a personal email automation tool. It was built by Sourabh Saxena and is used only by its developer, with the developer's own accounts. It is not offered to anyone else, and it does not collect data from other users.

## What InboxPilot accesses

**Gmail.** InboxPilot reads only the Gmail messages that carry the "InboxPilot" label in the developer's own account. An n8n automation workflow picks up these messages and passes them to InboxPilot. n8n is self-hosted in Docker on the developer's own server (on AWS EC2), and InboxPilot runs on that same server. InboxPilot never reads other messages.

**Google Calendar.** InboxPilot creates events in the developer's own Google Calendar for dates and deadlines found in those emails. Before it creates an event with a start time, it reads the calendar's free/busy information for that time to check for conflicts.

## How the data is used

For each labelled email:

1. **Classification with Groq.** The email's subject, sender and body, plus the text of any PDF or Word attachments, are sent to Groq's API. Groq's language model sorts the email and pulls out details such as a summary and any event date. If a PDF has no text in it (for example, a scanned document), each page is turned into an image and sent to Groq's vision model to read the text.
2. **Notion.** The result is written to the developer's own Notion workspace. It includes details from the email, such as the subject, sender, summary and dates.
3. **Google Calendar.** Events are created in the developer's own calendar, as described above.
4. **Telegram.** Alerts and a daily digest are sent to the developer's own Telegram chat. They can include email details such as the subject, sender, summary and dates.
5. **Duplicate check (Supabase).** A small Postgres database hosted on Supabase records which emails have already been processed, so the same email is never handled twice. Each record holds:
   - the Gmail message ID or, if an email has no ID, a SHA-256 fingerprint of its subject, sender and body. The fingerprint is a one-way hash, and the email text cannot be read back from it;
   - timestamps showing when it was processed;
   - a processing status and a random claim token, which stop two requests from handling the same email at once.

   The email's content is not stored in this database.

## Where data is kept

Email details are kept in the developer's own Notion workspace, Google Calendar and Telegram chat, and in the duplicate-check records described above. They stay there until the developer deletes them.

Some email data is also kept on the developer's own server. n8n keeps a history of recent workflow runs, including the email data those runs carried. InboxPilot's server logs record email subjects and senders so that problems can be investigated.

## Sharing

The data is sent only to the services listed above (Google, Groq, Notion, Telegram and Supabase), and only so that InboxPilot can work. It is never sold, never used for advertising and never shared with anyone else. InboxPilot does not use it to train or improve AI models.

InboxPilot's use of information received from Google APIs follows the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements.

## Revoking access

To remove InboxPilot's access to a Google account, go to your Google Account, then **Security**, then **Third-party apps & services** (or visit [myaccount.google.com/connections](https://myaccount.google.com/connections)). Select InboxPilot and remove its access.

## Contact

Questions about this policy: [sourabhsaxena2143@gmail.com](mailto:sourabhsaxena2143@gmail.com)
