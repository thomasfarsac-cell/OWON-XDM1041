# OWON-XDM1041
Interface complète pour multimètre OWON XDM : acquisition temps réel, graphiques avec marqueurs et stats, changement de mode SCPI, export CSV/PNG, mini-affichage flottant, test Go/No-Go automatique, limite Y ajustable et polling configurable (2 Hz par défaut).
🧭 XDM Scope — Interface de contrôle OWON XDM1041 / XDM1000

Application Python/Tkinter permettant de piloter et visualiser en temps réel les mesures d’un multimètre OWON XDM via port série SCPI.
Inspirée d’un analyseur logique, elle offre une interface fluide et complète pour la mesure, le logging et le diagnostic.

⚙️ Fonctionnalités principales

Connexion série manuelle ou automatique (détection via *IDN?)

Acquisition en temps réel avec graphique défilant

Changement de mode SCPI direct :
VOLT DC / VOLT AC / CURR DC / CURR AC / RES / CONT / CAP / DIOD / FREQ / TEMP

Vitesses d’échantillonnage (RATE) : boutons [S] [M] [F]

Marqueurs A–B avec stats (Δt, min, max, moyenne)

Affichage dynamique : valeur lisible, format humain, unité adaptée

Go / No-Go : test automatique avec tolérances ±0.5%, ±1%, ±5% ou personnalisée

Mini-affichage flottant (option fond transparent)

Export CSV et PNG

Limite Ymax personnalisable pour éviter les valeurs hors plage

Polling réglable (0.5–50 Hz, 2 Hz par défaut)

Durée d’affichage glissante réglable (par défaut 30 s)

🧰 Installation
pip install pyserial matplotlib


Puis lancer l’application :

python xdm_scope_v3_5.py

🪛 Compatibilité testée

OWON XDM1041 / XDM1000

Windows 10 / 11

Python ≥ 3.9

Interface série : 115200 bauds, 8N1

📦 Fichiers produits
Type	Description
xdm_scope_v3_5.py	Application principale
data_*.csv	Export des mesures
plot_*.png	Capture du graphique
🧾 Licence

Projet distribué sous licence MIT
.
Utilisation, modification et redistribution totalement libres, à des fins personnelles ou commerciales.

🤖 Génération du projet

Ce projet a été entièrement conçu et développé avec l’assistance de ChatGPT (GPT-5),
depuis la définition du concept jusqu’à la création du code Python complet,
l’intégration des commandes SCPI et la mise en forme de l’interface utilisateur.
