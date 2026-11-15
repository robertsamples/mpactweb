# Troubleshooting

## Input files

**"No biological groups were found" or a missing-column error.**
The sample list needs columns named exactly `Injection` and `Sample_Code`, and
the metadata needs `Sample_Code` and `Biological_Group`. Check the spelling and
capitalization of the header row.

**Injections in the peak table are not in the sample list.**
Injection names must match the peak table's column headers exactly, including
any date prefix or file extension your software adds. The
[sheet editor](../setup/file-selection.md#creating-the-sample-list-and-metadata)
reads names directly from the peak table, which avoids this.

**The peak table format is not recognized.**
Re-export with the settings in [Input files](../setup/input-files.md#peak-table).
For MS-DIAL, export the alignment result (Area), not a single-sample peak list.
For an unsupported program, convert the table to the
[Progenesis layout](../setup/input-files.md#other-software).

**No MS2 spectra in the details panel.**
Upload a fragment database (`.msp` or `.mgf`) exported from the same alignment
as the peak table. Spectra are matched to features by compound ID, so a
fragment file from a different alignment will not line up.

## Results

**A result tab is greyed out.**
Result tabs open only for a completed analysis. Check the chip in the top bar;
if the run failed, the **Run** tab shows the error in its log.

**Very few features pass filtering.**
Look at the filtering table on [Data Review](../results/data-review.md#summary)
to see which filter removes the most. Common causes are the wrong blank group,
a relative threshold set too high, or a CV threshold too strict for a small or
chemically diverse dataset.

**The MS1 or MS/MS search buttons are missing.**
The server does not have the reference data installed. See
[Reference data](../self-hosting/index.md#reference-data).

**A link to an analysis says it is no longer available.**
Analyses belong to the workspace that ran them, and old workspaces are deleted
after a period of inactivity. Links do not open someone else's analysis.

## Sign-in

**The sign-in code email does not arrive.**
Check the spam folder. Codes expire after 15 minutes and only a few can be sent
per hour, so wait before requesting another. Server operators: confirm the
`MPACT_SMTP_*` settings and that outgoing port 587 is open.

**Signed out after the site moved to a new address.**
Sign-ins are tied to the site's address. Sign in again; your workspace and runs
are kept with your account.

## Running your own server

**`fetch_reference_data.py` fails with HTTP 403.**
npatlas.org sometimes refuses Python's default client. Download the table with
`curl` instead and save it as `npatlas.tsv` in the reference directory:

```bash
curl -L -o server/data/npatlas.tsv "https://www.npatlas.org/static/downloads/NPAtlas_download.tsv"
```

**The Docker build fails at `vendor_core.py verify`.**
A file under `server/mpact_core/` differs from the pinned copy. Those files
must not be edited in place; see [Development](development.md#the-vendored-pipeline).
On Windows, also check that Git has not changed their line endings.

**Uploads over 100 MB fail behind Cloudflare.**
Cloudflare's proxy limits request bodies to 100 MB on its free plan. Set the
DNS record to DNS-only so uploads go straight to your server.
