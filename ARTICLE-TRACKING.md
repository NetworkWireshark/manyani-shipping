Article tracking update
=======================

Use your own username: aissa, saed, khalid, or amine. Passwords for these accounts
are in data/article-user-credentials.txt. Existing accounts and passwords are preserved.

Click COMMANDE to open the existing order list. Charger marks an article as
loaded and records the logged-in username and time. Click Chargé again to undo
loading. The commande list has no Livraison column and no delivery progress.

Double-click Quantite, Prix unitaire, Remise, Montant, or Total net, then Confirmer.
A new Remise, for example 25% changed to 10%, is applied to every article in
that order and each Total net follows. There is no MODIFIER or HISTORIQUE button.

Open lists synchronize approximately every four seconds. If another user changes
an article after you open the editor, your edit is rejected. Close and reopen
MODIFIER, review the new quantity, and confirm again. Each article has its own version.

The central database is unchanged in location. Additive tables retain original
imports and append-only history. Before-migration database and account backups
are in data/backups. No prior username can be recovered for legacy delivered flags.

Activation: the current Windows server processes deny stop access from this
session. Right-click Restart-Tracking.bat and select Run as administrator. It only
restarts Python listeners on ports 80/8080 after checking that they serve this
application's login page. Then press Ctrl+F5 in the browser or Actualiser on Android.
The existing Android app loads these shared server pages; no APK change is required.

Validation: python -m unittest discover -s tests -v
Browser validation (development dependency: playwright): python tests/check_article_ui.py
Includes desktop and Android-sized browser layouts; no physical Android device test.
