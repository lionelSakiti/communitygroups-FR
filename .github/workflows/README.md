# Action GitHub : nouvelles propositions Sessionize

Cette action GitHub interroge régulièrement l'API Sessionize pour détecter les nouvelles propositions de sessions et envoie une notification dans un canal Slack.

## Présentation

L'action s'exécute selon une planification (toutes les heures entre 8 h et 20 h CST, du lundi au vendredi, selon la description d'origine). Elle peut également être lancée manuellement au moyen de `workflow_dispatch`.

Elle effectue quatre opérations :

1. Récupérer l'identifiant de la dernière session dans Sessionize.
2. Le comparer à l'identifiant précédemment enregistré.
3. Envoyer des notifications dans le canal Slack configuré lorsque de nouvelles sessions sont détectées.
4. Mettre à jour l'identifiant enregistré pour les prochaines comparaisons.

## Configuration

Configurez les secrets et variables suivants dans votre dépôt GitHub :

1. Ouvrez les **Settings** du dépôt.
2. Accédez à **Secrets and variables → Actions**.
3. Ajoutez les secrets et variables indiqués ci-dessous.

### Secrets nécessaires

- `SESSIONIZE_SESSION_LIST_URL` : URL permettant de récupérer les sessions depuis Sessionize.

Pour obtenir cette URL :

1. Connectez-vous à votre compte Sessionize.
2. Ouvrez l'onglet **API / Embed**.
3. Cliquez sur **Create new endpoint**.
4. Choisissez le format **JSON**.
5. Dans **Includes sessions**, choisissez **All Except Declined**.
6. Cliquez sur **Create**.
7. Cliquez sur **Get Code**.
8. Copiez l'URL de la liste des sessions, qui se termine par `/view/Sessions`.

- `SLACK_WEBHOOK_URL` : URL du webhook de votre canal Slack.
- `REPO_ACCESS_TOKEN` : jeton GitHub autorisé à modifier les variables du dépôt.

**Remarque :** pour configurer le webhook Slack, copiez [cette automatisation Slack](https://slack.com/shortcuts/Ft091QD812HE/e8cce7d6069eb6243181f5050a0f071c), puis utilisez l'URL fournie dans le secret `SLACK_WEBHOOK_URL`.

### Variable nécessaire

- `SESSIONIZE_LATEST_SESSION_ID` : identifiant de la dernière session traitée.

## Fonctionnement

1. L'action récupère toutes les sessions de Sessionize et identifie l'identifiant le plus récent.
2. Elle le compare à `SESSIONIZE_LATEST_SESSION_ID`.
3. Si de nouvelles sessions sont détectées, elle transmet à Slack leur identifiant, titre, description et noms des intervenants.
4. Elle met ensuite à jour l'identifiant enregistré.

**Remarque :** si aucun identifiant précédent n'est enregistré, celui-ci est initialisé sans envoyer de notification.

## Planification

L'action est exécutée :

- Toutes les heures entre 8 h et 20 h CST, selon la documentation source.
- Du lundi au vendredi.
- Ou manuellement via `workflow_dispatch`.

## Format de sortie

Les notifications Slack contiennent les informations suivantes :

```json
{
  "id": "session_id",
  "title": "session_title",
  "description": "session_description",
  "speakers": "speaker1, speaker2, ..."
}
```

## Gestion des erreurs

- Aucune notification Slack n'est envoyée si l'identifiant précédent est vide.
- L'identifiant enregistré continue d'être actualisé même lorsqu'aucune nouvelle session n'est détectée.
- Une pause de trois secondes sépare les notifications Slack afin de limiter le risque de dépassement des quotas d'appels.
