# Mes cles Gemini - interface navigateur

1. Double-clique sur Lancer-Gemini.cmd.
2. Ajoute tes adresses Google dans la page, une par ligne.
3. Connecte chaque compte dans la page officielle Google qui s'ouvre si necessaire.
4. La file cree le projet Google Cloud, le compte de service et la cle AQ, puis actualise cles-gemini-AQ.txt. Le bouton Telecharger fournit une cle par ligne.

Le mot de passe se saisit uniquement sur Google. L'application ne collecte pas les mots de passe et ne simule ni appareil mobile ni validation du compte. Elle utilise les API officielles pour creer la cle ; elle ne pilote pas les clics dans AI Studio. Google peut demander une confirmation ou des conditions a accepter. Aucun compte de facturation n'est rattache.

## Reprise apres coupure

Les adresses, identifiants de projets et etapes sont conserves dans gemini-web/comptes.sqlite3. Les cles sont chiffrees avec la protection du compte Windows. L'export TXT contient les cles en clair.

Apres un arret brutal, relance Lancer-Gemini.cmd. Les operations interrompues sont remises dans la file. Avant de creer un projet, un compte de service ou une cle, l'application recherche la ressource existante avec son identifiant stable. Si seule l'ecriture du document a ete interrompue, il est regenere. Les comptes termines ne sont pas recrees.

La pause laisse terminer le compte en cours et se conserve au redemarrage. Un compte en erreur ne bloque pas les suivants : clique sur Reprendre ce compte apres avoir resolu la cause indiquee. Une session expiree ou une validation Google interrompue peut exiger une nouvelle connexion. Le PC eteint ne peut pas continuer a travailler : il reprend au lancement suivant.

Ne supprime ni comptes.sqlite3 ni le dossier ../work/google-runtime/connexion. Ne deplace pas les fichiers pendant le traitement. Les anciennes donnees de Gemini-Comptes.ps1 ne sont pas importees dans la nouvelle application. Aucune cle reelle n'avait ete creee avant ce changement.

## Execution locale

La page est accessible sur ce PC uniquement, a l'adresse 127.0.0.1:8765. Utilise le lanceur pour ouvrir la session. Il prepare Google Cloud CLI si besoin, puis demarre le serveur en arriere-plan. Fermer l'onglet n'arrete pas le serveur. Relancer le lanceur retrouve une instance deja active.

Windows 64 bits et Internet sont necessaires pour Google. La version navigateur utilise le Python fourni avec Google Cloud CLI. Les controles Google restent obligatoires. Un compte cree sur mobile peut etre connecte normalement sur PC.

## Verification

Huit tests automatises avec Google simule couvrent la file, les doublons, une coupure apres creation distante de la cle, une coupure avant l'export, une connexion refusee, la persistance de la pause, les entrees invalides et le prefixe AQ. Ils ne prouvent pas qu'un compte reel sera accepte par Google. Le chiffrement Windows doit fonctionner dans la session Windows habituelle ; il est teste avant toute creation distante.

Sources officielles :
- https://ai.google.dev/gemini-api/docs/api-key
- https://cloud.google.com/sdk/gcloud/reference/auth/login
- https://cloud.google.com/sdk/gcloud/reference/services/api-keys/create
