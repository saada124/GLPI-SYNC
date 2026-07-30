# ⚡ Guide Ultra-Rapide de Configuration (GLPI ↔ AppSheet)

Ce guide est spécialement conçu pour être **simple, rapide et facile à suivre**.
Suivez ces 5 étapes chrono pour connecter votre GLPI à Google Sheets / AppSheet !

---

## 🔑 Étape 1 : Activation de l'API dans GLPI (2 min)

1. Connectez-vous à **GLPI** avec votre compte administrateur.
2. Allez dans le menu : **Configuration > Général > API**.
3. Activez les options suivantes (Mettre sur **OUI**) :
   - [x] **Activer la connexion par API REST**
   - [x] **Activer la connexion par jeton d'application**
4. Cliquez sur le bouton **Sauvegarder**.

### 1. Récupérer votre 1ère clé (`GLPI_APP_TOKEN`)
- Sur cette même page API, descendez jusqu'à **Clés d'application API**.
- Cliquez sur le petit **`+`** (*Ajouter*).
- Donnez le nom : `Sync`
- Cliquez sur **Ajouter**.
- ➔ **Copiez la clé générée** (*C'est votre `GLPI_APP_TOKEN`*).

### 2. Récupérer votre 2ème clé (`GLPI_USER_TOKEN`)
- Cliquez sur votre nom d'utilisateur tout en haut à droite (**Mon compte**).
- Descendez jusqu'à la section **Clé d'API personnelle**.
- Cliquez sur **Générer une clé d'API**.
- ➔ **Copiez cette clé personnelle** (*C'est votre `GLPI_USER_TOKEN`*).

---

## 📊 Étape 2 : Configuration Google Sheets (1 min)

1. Ouvrez votre fichier **Google Sheets**.
2. Dans le menu du haut, cliquez sur : **Extensions > Apps Script**.
3. **Effacez tout** le code présent à l'écran.
4. Ouvrez le fichier [`src/webhook/Code.gs`](file:///c:/Users/talel/Desktop/GLPI%20SYNC/src/webhook/Code.gs) de ce projet, **copiez** son contenu et **collez-le** dans Apps Script.
5. Cliquez sur le bouton bleu **Déployer** (en haut à droite) > **Nouveau déploiement**.
6. Sélectionnez : **Application Web**.
   - **Exécuter en tant que** : *Moi*
   - **Qui a accès** : *N'importe qui*
7. Cliquez sur **Déployer** et acceptez les autorisations de Google.
8. ➔ **Copiez l'URL fournie** (*C'est votre `SHEETS_WEBHOOK_URL`*).

---

## ⚙️ Étape 3 : Créer le fichier de configuration `.env` (1 min)

À la racine du dossier du projet, créez un fichier nommé `.env`.  
Copiez et collez le bloc suivant à l'intérieur, puis remplacez avec vos clés :

```env
GLPI_URL=http://localhost/glpi/apirest.php/
GLPI_APP_TOKEN=COLLEZ_VOTRE_APP_TOKEN_ICI
GLPI_USER_TOKEN=COLLEZ_VOTRE_USER_TOKEN_ICI

SHEETS_WEBHOOK_URL=COLLEZ_VOTRE_URL_GOOGLE_APPS_SCRIPT_ICI
SHEETS_AUTH_TOKEN=glpi-sync-secret

APP_TIMEZONE=Etc/GMT-1
SYNC_INTERVAL_MINUTES=10
LOG_LEVEL=INFO
```

> [!NOTE]
> Modifiez `GLPI_URL` par l'adresse de votre serveur GLPI si elle est différente.

---

## 🛠️ Étape 4 : Créer les intitulés dans GLPI en 1 clic (30 sec)

Pour créer automatiquement toutes les catégories (*Informatique, Caisse, Imprimante...*) et types de matériels dans GLPI :

1. Ouvrez une invite de commande (Terminal / PowerShell) dans ce dossier.
2. Tapez la commande suivante et appuyez sur **Entrée** :

```powershell
venv\Scripts\python seed_glpi_dropdowns.py
```

*(Le script va tout créer proprement dans GLPI sans que vous n'ayez rien à faire à la main !)*

---

## 🚀 Étape 5 : Lancer la synchronisation ! (10 sec)

### 🔹 Pour tester une première fois (Exécution unique)
Dans votre terminal, tapez :

```powershell
venv\Scripts\python src\main.py --once
```

Vous verrez apparaître les informations de synchronisation à l'écran.

### 🔹 Pour lancer la synchro en continu (Arrière-plan)
Il vous suffit de double-cliquer sur le fichier :

👉 **`run_sync.bat`**

La synchronisation tournera automatiquement toutes les 10 minutes !

---

🎉 **Bravo, votre synchronisation GLPI ↔ AppSheet est opérationnelle !**
