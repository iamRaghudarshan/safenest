===============================================================================
  FinMate — portable bundle
===============================================================================

This folder contains everything needed to run FinMate on another computer:
your personal finance records, documents, photo gallery and password vault.

It works on Windows, macOS and Linux.


-------------------------------------------------------------------------------
  HOW TO START IT
-------------------------------------------------------------------------------

  Windows      Double-click:   Start FinMate (Windows).bat

  Mac          Double-click:   Start FinMate (Mac).command

               The first time, macOS may say the file "cannot be opened
               because it is from an unidentified developer". If so:
               right-click the file -> Open -> Open. You only do this once.

  Linux        Open a terminal here and run:   python3 setup.py

The first run opens a SETUP WINDOW that asks a few questions, then installs what
it needs (a few minutes). Every run after that starts straight away.

You do not need to type anything into a black terminal window. If the setup
window cannot open — a computer with no screen, or a Python built without the Tk
toolkit — the same questions are asked at the prompt instead, and the answers
mean exactly the same thing. To force that on purpose, add  --no-gui

When it's running, leave the window open. FinMate opens in your browser at
http://127.0.0.1:8080 — closing the window stops the app.

If you chose the launcher for the wrong kind of computer, the other one is in the
"for-the-other-platform" folder — move it out and use that instead.


-------------------------------------------------------------------------------
  MOVING FROM A WINDOWS PC TO A MacBook
-------------------------------------------------------------------------------

Do this on the Windows PC that runs FinMate:

  1. On your phone (or on the PC), open FinMate -> Profile -> "Take my data to
     another computer" (or, as an admin, "Move everything to another computer").

  2. Choose  Mac.  Leave "Include my data" ticked. Tap "Create the copy".

  3. When it finishes it shows a file name ending in .zip and the full path,
     with a "Copy full path" button. It is normally on the Desktop, e.g.
        C:\Users\<you>\Desktop\FinMate-for-Mac-2026-07-29.zip

     >> Copy the .ZIP, not the folder. <<
     The zip is the only thing that carries the "runnable" flag macOS needs;
     copy the loose folder and Finder will refuse to start the launcher.

  4. Plug in a USB drive and copy that .zip onto it.

     If the USB drive was formatted on Windows as NTFS, a Mac can still READ it,
     which is all you need here. If you hit trouble, format the drive as exFAT —
     both Windows and macOS read and write exFAT.

Now on the MacBook:

  5. Plug the drive in and drag the .zip to somewhere permanent, such as your
     home folder or Documents. Do NOT run it from the USB drive.

  6. Double-click the .zip. macOS unpacks it into a folder called
     FinMate-for-Mac.

  7. Open that folder and double-click:  Start FinMate (Mac).command

     The first time, macOS will likely say it "cannot be opened because it is
     from an unidentified developer". That is normal for any script that did not
     come from the App Store. To get past it:
         right-click (or Control-click) the file -> Open -> Open

     On newer macOS you may instead need:
         System Settings -> Privacy & Security -> scroll down -> "Open Anyway"

  8. A Terminal window opens and asks a few questions (see below). Answer them
     and FinMate starts, opening in your browser at http://127.0.0.1:8080

  9. Keep that Terminal window open while you use FinMate. Closing it stops the
     app. To start it again later, just double-click the .command file.

 10. Once everything works on the Mac, delete the .zip from the USB drive — it
     contains your files and your vault key.

If Terminal says Python is missing, run this in Terminal and try again:
     xcode-select --install

TWO THINGS THAT CATCH PEOPLE OUT ON A MAC:

  * A MacBook sleeps when you close the lid. Always — being plugged in makes no
    difference, and "caffeinate" does not stop it either (that only blocks the
    idle timer). While it sleeps, FinMate is unreachable from your phone and
    from your web address. Leave the lid open, plug in an external display, or
    install something like Amphetamine.

  * If you use your own web address, stop FinMate on the OLD computer first.
    Both machines serving the same tunnel means Cloudflare answers from
    whichever it feels like, out of two different databases.


-------------------------------------------------------------------------------
  WHAT IT ASKS YOU
-------------------------------------------------------------------------------

The setup window walks through this in three steps: where your data lives, how
FinMate is reached, and your sign-in. The same questions in the same order.

  Where to keep your data      Press Enter for the "data" folder next to this file.

  Which database               Press Enter for the built-in one. Nothing to install.

  Which port                   Press Enter for 8080 unless something else uses it.

  Devices on your Wi-Fi        Say yes so your phone can open FinMate over Wi-Fi.

  Reachable from outside?      Three choices:

      1. No — this computer and my Wi-Fi only.
         The safest, and the right answer if you're unsure.

      2. Yes — my own Cloudflare Tunnel.
         Keeps your own address, e.g. https://finmate.yourdomain.com

         If this bundle was made WITH your data, the tunnel travelled with it.
         Setup offers "keep the address this copy came with", shows you the
         address, and sets it up itself. There is nothing to type and nothing
         to fetch off the old computer.

         Only if it did NOT travel (a bundle made without data, or a new
         tunnel) does it ask for TWO things:
           * the tunnel TOKEN — Cloudflare dashboard -> Zero Trust ->
             Networks -> Tunnels -> your tunnel -> Configure. It's the long
             string in the install command shown there.
           * your public web address.

         Either way, cloudflared must be installed first:
             Windows   winget install --id Cloudflare.cloudflared
             Mac       brew install cloudflared
         Setup tells you if it's missing and carries on locally meanwhile.
         Install it and run the launcher again — nothing is lost, and your
         answers are remembered.

         !! ONE COMPUTER AT A TIME !!
         A tunnel can only be served by one machine. Run it on two and
         Cloudflare splits traffic between them at random — and as each machine
         has its own separate database, records will appear and vanish
         depending on which one happened to answer. Stop FinMate on the old
         computer before starting it on the new one.

      3. Yes — a temporary free link.
         Cloudflare hands out a random address with no account needed. It
         changes every time you restart, so it's for trying things out.

  Your admin account           Only asked when the database is empty. If you
                               brought your data across, your existing sign-ins
                               keep working.


-------------------------------------------------------------------------------
  WHAT IT NEEDS
-------------------------------------------------------------------------------

Nothing, if you have an internet connection. The launcher installs whatever is
missing by itself:

  * Python 3.10 or newer — installed automatically.
      Windows — via winget, which comes with Windows 10 and 11.
      Mac     — raises Apple's own installer, then waits for it to finish.
    Only if that fails does it fall back to asking you to install it by hand:
      Windows   https://www.python.org/downloads/  (TICK "Add python.exe to
                PATH" on the first installer screen)
      Mac       open Terminal and run:  xcode-select --install

  * The Python libraries FinMate uses — installed automatically on first run.

  * cloudflared, if you chose a web address — downloaded automatically into
    this folder. No Homebrew, no winget, no administrator rights needed. It is
    not carried in the bundle because the file is built for one operating
    system and one processor, so each computer fetches the right one.

  * An internet connection, for the FIRST run only. After that it runs
    completely offline.

  * About 500 MB of free disk space, plus room for your photos.

You do NOT need to install a database. FinMate keeps everything in a single
file inside the "data" folder.


-------------------------------------------------------------------------------
  YOUR DATA
-------------------------------------------------------------------------------

Everything lives in the "data" folder next to this file:

    data/finmate.db          your records, in one file
    data/media/              your photos and documents
    data/carried-secrets.env the key your password vault is encrypted with

To move FinMate to yet another computer later, copy this whole folder again.
To back it up, copy the "data" folder somewhere safe.

  !! IMPORTANT !!
  If this bundle was made WITH your data, it contains your personal files and
  the encryption key for your saved passwords. Treat it like a password:

    - Move it on a USB drive, or an encrypted transfer.
    - Do not email it, and do not leave it in a shared cloud folder.
    - Delete the copy from the USB drive once it's in place.

  Never delete or edit data/carried-secrets.env or backend/.env by hand. Losing
  the vault key makes every saved password permanently unreadable — there is no
  recovery, by design.


-------------------------------------------------------------------------------
  RUNNING IT FROM AN EXTERNAL HARD DISK
-------------------------------------------------------------------------------

Yes, this works. Copy this whole folder onto the external drive and start it
from there. The drive then holds the app, your photos and your database, and the
laptop's own disk stays free.

Note this is a different thing from the USB step in the "moving to a MacBook"
instructions above. There the USB drive is only a courier and you copy the files
off it. Here the external drive is where FinMate actually lives, permanently.

  Format the drive as exFAT if the Mac will ever touch it.
      Windows and macOS both read AND write exFAT. A drive formatted NTFS is
      read-only on a Mac, so FinMate can start but cannot save anything.
      Reformatting erases the drive — do it before you copy anything across.

  The drive must be plugged in before you start FinMate.
      Your photos and database live on it. Start the app without the drive and
      it will not find them.

  Never unplug the drive while FinMate is running.
      Stop the app first (close its window), then eject the drive properly. The
      database is being written to; pulling the cable mid-write can damage it.

  It will be slower than the internal disk, in two places you will notice:
      - the first start of each session, loading the photo-recognition models
      - the background pass that reads photos for faces and for search
      Everyday use feels normal. A spinning disk on USB 2 is the slow case; an
      SSD on USB 3 is close to internal speed. The photo pass carries on where
      it left off, so a slow first run is a one-off, not a permanent tax.

  The drive letter changing is handled.
      Windows may call the same drive E: today and F: tomorrow, depending on
      what else is plugged in. FinMate stores its paths relative to its own
      folder, so it keeps working. If you move the folder somewhere else on the
      drive, run the launcher with --reconfigure.

  Back it up anyway.
      An external drive is one drive. Copy the "data" folder somewhere else
      periodically — see YOUR DATA above.


-------------------------------------------------------------------------------
  USING IT FROM YOUR PHONE
-------------------------------------------------------------------------------

During setup, answer "yes" to letting other devices connect. The launcher then
prints a second address like:

    On your phone : http://192.168.1.7:8080

Open that in your phone's browser while on the same Wi-Fi. In Safari or Chrome
choose "Add to Home Screen" and FinMate behaves like an installed app.

The computer must be switched on and awake for the phone to reach it.


-------------------------------------------------------------------------------
  COMMON QUESTIONS
-------------------------------------------------------------------------------

  "It says the port is already in use."
      Something else is on port 8080. Run the launcher again with the
      --reconfigure option, or edit the "port" value in finmate-config.json.

  "I want to change my answers."
      Windows:  Start FinMate (Windows).bat --reconfigure
      Mac:      ./"Start FinMate (Mac).command" --reconfigure

  "I forgot my password."
      Open a terminal in the "backend" folder and run:
          ../.venv/bin/python create_admin.py            (Mac/Linux)
          ..\.venv\Scripts\python.exe create_admin.py    (Windows)
      Entering an existing email resets that account's password.

  "How do I stop it?"
      Close the window, or press Ctrl+C in it.

  "Is anything sent to the internet?"
      No. FinMate runs entirely on your own computer. The only network access
      is the one-off dependency download during the first setup.

  "Which timezone are the dates in?"
      India Standard Time (UTC+05:30), always — including on a Mac set to a
      different timezone. Times show as 12-hour with AM/PM. This is deliberate:
      the same record must not read as two different times depending on which
      machine you open it from.

  "Finder won't open the .command file."
      Right-click it -> Open -> Open. If that fails you probably copied the
      loose folder instead of the .zip; go back and copy the .zip.

  ---------------------------------------------------------------
  (c) 2026 FinMate. All rights reserved.
  Licensed to the person named in your licence file. Not for resale
  or redistribution.
