/**
 * Office Script: "Sync registrations to site"
 *
 * Paste into the "Registration for a Library Event" Excel (Automate → New Script),
 * save, then press Run whenever you want the site to show the latest registrations.
 * It sends the whole responses table; the site replaces what it had, so edits and
 * deleted rows in Excel are reflected too.
 *
 * Put the same value as REGISTRATIONS_SYNC_TOKEN from the server .env into TOKEN.
 * Run it from Excel itself: when started from Power Automate, Office Scripts
 * cannot call outside websites.
 */
async function main(workbook: ExcelScript.Workbook) {
  const SITE = "https://library.coventry.edu.kz/api/registrations/sync";
  const TOKEN = "PASTE_REGISTRATIONS_SYNC_TOKEN_HERE";

  const table = workbook.getTables()[0];
  if (!table) throw new Error("No table found — the Forms responses table is missing.");

  const headers = table.getHeaderRowRange().getTexts()[0].map((h) => h.trim().toLowerCase());
  const col = (...starts: string[]) => headers.findIndex((h) => starts.some((s) => h.startsWith(s)));
  const iId = col("id");
  const iDone = col("completion time");
  const iEmail = col("email");
  const iName = col("name");
  const iYourName = col("your name");
  const iTitle = col("the name of the event");
  const iEventId = col("event_id", "event id");
  if (iId < 0 || iEventId < 0) throw new Error("Columns 'Id' and 'Event_ID' are required.");

  const body = table.getRangeBetweenHeaderAndTotal();
  const rows = body.getRowCount() ? body.getTexts() : [];

  const payload = rows
    .filter((r) => r[iId] !== "")
    .map((r) => ({
      id: r[iId],
      submitted_at: iDone >= 0 ? r[iDone] : "",
      email: iEmail >= 0 ? r[iEmail] : "",
      // "Your Name" is what the person typed; "Name" is their Microsoft account.
      name: (iYourName >= 0 && r[iYourName]) || (iName >= 0 ? r[iName] : ""),
      event_title: iTitle >= 0 ? r[iTitle] : "",
      event_id: r[iEventId],
    }));

  const res = await fetch(SITE, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token: TOKEN, rows: payload }),
  });
  const result: { ok: boolean; saved?: number; error?: string; unknown_event_ids?: number[] } = await res.json();
  if (!result.ok) throw new Error("Site refused the sync: " + result.error);

  console.log(`Sent ${result.saved} registrations to the site.`);
  if (result.unknown_event_ids && result.unknown_event_ids.length) {
    console.log("Event IDs not found on the site: " + result.unknown_event_ids.join(", "));
  }
}
