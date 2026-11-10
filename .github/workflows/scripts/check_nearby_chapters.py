#!/usr/bin/env python3
"""
Vérifier les chapitres CNCF Community Group voisins à l'ouverture d'une demande de chapitre.
"""

import os
import re
import sys
import time
import requests
from geopy.geocoders import Nominatim
from geopy.distance import geodesic
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

# Distance maximale pour considérer un chapitre comme « voisin ».
DISTANCE_THRESHOLD_KM = 100
DISTANCE_THRESHOLD_M = DISTANCE_THRESHOLD_KM * 1000

# API de recherche JSON Open Community Groups. Le programme CNCF est passé de
# community.cncf.io à ocgroups.dev. Ce point d'accès effectue la recherche par distance
# côté serveur, à partir des coordonnées transmises via les en-têtes CloudFront-Viewer-*
# et des paramètres distance + sort_by=distance. Il ne renvoie que les groupes
# dans le rayon indiqué, triés par proximité. Cela évite de télécharger tous les groupes
# et de calculer les distances localement, même lorsque leur nombre augmente.
# Voir https://github.com/cncf/open-community-groups (route : /explore/groups/search).
CHAPTERS_API_URL = "https://ocgroups.dev/explore/groups/search"
CHAPTERS_COMMUNITY = "cncf"
# Limite du nombre de résultats demandés. Les groupes voisins sont généralement peu nombreux ;
# cette limite de sécurité correspond au MAX_PAGINATION_LIMIT de 100 côté serveur.
RESULTS_LIMIT = 100
# Gestion des erreurs de l'API. La vérification étant indicative, une erreur temporaire
# entraîne plusieurs essais espacés progressivement. Si l'erreur persiste, main() renvoie
# le statut « error » et le workflow propose de commenter /recheck.
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2
# En-têtes dans lesquels le serveur OCG lit les coordonnées. Les mainteneurs
# indiquent que leurs noms pourraient changer.
VIEWER_LATITUDE_HEADER = "CloudFront-Viewer-Latitude"
VIEWER_LONGITUDE_HEADER = "CloudFront-Viewer-Longitude"
# Seuls les chapitres régionaux sont pertinents pour la recherche de proximité. Leurs
# identifiants sont transmis à l'API comme filtres group_category afin de ne récupérer
# que ces groupes (voir fetch_nearby_chapters). normalize_chapter vérifie également
# leur catégorie par sécurité, si le filtre serveur cessait de fonctionner. La comparaison
# porte sur l'identifiant de catégorie, stable même si son nom affiché change.
REGION_SPECIFIC_CATEGORY_SLUGS = {"regional"}

def extract_location_from_issue(issue_body):
    """
    Extraire la ville ou le lieu du corps de l'issue GitHub.
    Le lieu figure dans le champ anglais ou français consacré à la ville du CNCG.
    """
    if not issue_body:
        return None

    # Expression régulière correspondant au champ de localisation du formulaire
    # Rechercher la section du nom de ville en anglais ou en français
    pattern = r'###\s*(?:City or location name for your CNCG|Nom de la ville ou du lieu de votre CNCG)\s*\n\s*(.+?)(?:\n\n|\n###|$)'
    match = re.search(pattern, issue_body, re.IGNORECASE | re.DOTALL)

    if match:
        location = match.group(1).strip()
        # Supprimer les préfixes courants comme « e.g. » ou « Cloud Native »
        location = re.sub(r'^(e\.g\.\s*|Cloud Native\s*)', '', location, flags=re.IGNORECASE)
        return location.strip()

    # Solution de repli : chercher une valeur après l'en-tête de localisation
    lines = issue_body.split('\n')
    for i, line in enumerate(lines):
        if ('City or location name' in line or 'location name for your CNCG' in line.lower() or 'Nom de la ville ou du lieu de votre CNCG' in line):
            # Récupérer la prochaine ligne non vide
            for j in range(i + 1, len(lines)):
                potential_location = lines[j].strip()
                if potential_location and not potential_location.startswith('#'):
                    potential_location = re.sub(r'^(e\.g\.\s*|Cloud Native\s*)', '', potential_location, flags=re.IGNORECASE)
                    return potential_location.strip()

    return None

def get_coordinates(location):
    """
    Obtenir la latitude et la longitude d'un lieu à l'aide de geopy.
    """
    try:
        geolocator = Nominatim(user_agent="cncf-chapter-checker/1.0")
        location_data = geolocator.geocode(location, timeout=10)

        if location_data:
            return (location_data.latitude, location_data.longitude)
        return None
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        print(f"Erreur de géocodage pour '{location}': {e}", file=sys.stderr)
        return None

def normalize_chapter(group):
    """
    Convertir un résultat de recherche Open Community Groups au format de chapitre
    attendu : {name, location, url, latitude, longitude}. Renvoyer None pour
    les chapitres non régionaux ou dépourvus de nom ou d'URL exploitable.
    """
    # Ignorer les groupes non régionaux (virtuels, thématiques ou liés à un projet hébergé),
    # car ils ne correspondent pas à un lieu. La requête API
    # applique déjà le filtre : cette vérification constitue une précaution.
    category_slug = (group.get('category') or {}).get('slug')
    if category_slug not in REGION_SPECIFIC_CATEGORY_SLUGS:
        return None

    name = group.get('name', '')
    city = group.get('city')
    country = group.get('country_name')

    # Lieu lisible affiché dans le commentaire publié.
    if city and country:
        location = f"{city}, {country}"
    else:
        location = city or country or ''

    # URL publique du groupe : /{community}/group/{slug}. L'identifiant convivial
    # défini par l'administration est prioritaire, conformément à public_slug() côté serveur.
    community = group.get('community_name') or CHAPTERS_COMMUNITY
    slug = group.get('slug_pretty') or group.get('slug')
    url = f"https://ocgroups.dev/{community}/group/{slug}" if slug else ''

    if not (name and url):
        return None

    return {
        'name': name,
        'location': location,
        'url': url,
        'latitude': group.get('latitude'),
        'longitude': group.get('longitude'),
    }


def fetch_nearby_chapters(latitude, longitude):
    """
    Interroger l'API Open Community Groups pour les chapitres CNCF situés à moins de
    DISTANCE_THRESHOLD_KM des coordonnées, triés du plus proche au plus éloigné. Le
    serveur filtre par distance ; les coordonnées sont transmises dans les en-têtes.
    Réessayer plusieurs fois en cas d'échec généralement temporaire. Renvoyer
    une liste de chapitres normalisés, ou None si toutes les tentatives échouent.
    """
    headers = {
        VIEWER_LATITUDE_HEADER: str(latitude),
        VIEWER_LONGITUDE_HEADER: str(longitude),
    }
    # community et group_category sont des filtres de tableau ; serde_qs attend un format
    # indexé (community[0]=..., group_category[0]=...). Le filtrage par catégorie
    # côté serveur allège la requête et évite surtout que les résultats
    # soient saturés par des groupes non régionaux possédant des coordonnées
    # au détriment des chapitres régionaux pertinents.
    params = [
        ('community[0]', CHAPTERS_COMMUNITY),
        ('distance', DISTANCE_THRESHOLD_M),
        ('sort_by', 'distance'),
        ('limit', RESULTS_LIMIT),
    ]
    params += [
        (f'group_category[{i}]', slug)
        for i, slug in enumerate(sorted(REGION_SPECIFIC_CATEGORY_SLUGS))
    ]

    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            response = requests.get(CHAPTERS_API_URL, params=params, headers=headers, timeout=30)
            response.raise_for_status()
            groups = response.json().get('groups', [])
            chapters = [c for c in (normalize_chapter(g) for g in groups) if c]
            print(f"L'API a renvoyé {len(groups)} groupes voisins ({len(chapters)} régionaux)", file=sys.stderr)
            return chapters
        except (requests.RequestException, ValueError) as e:
            print(f"Échec de la tentative {attempt}/{RETRY_ATTEMPTS} d'accès à l'API des chapitres : {e}", file=sys.stderr)
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    print(f"Abandon après {RETRY_ATTEMPTS} tentatives d'accès à l'API des chapitres", file=sys.stderr)
    return None

def annotate_distance(requested_coords, chapters):
    """
    Ajouter à chaque chapitre le champ « distance_km » (distance depuis requested_coords)
    s'il possède des coordonnées, sinon l'exclure. Ce champ sert à l'affichage ; l'API a
    déjà limité les résultats au rayon autorisé.
    """
    annotated = []
    for chapter in chapters:
        lat, lon = chapter.get('latitude'), chapter.get('longitude')
        if lat is None or lon is None:
            continue
        distance = geodesic(requested_coords, (lat, lon)).kilometers
        annotated.append({**chapter, 'distance_km': round(distance, 2)})
    return annotated

def format_output(nearby_chapters):
    """
    Mettre en forme les chapitres voisins en Markdown pour le commentaire GitHub.
    """
    if not nearby_chapters:
        return ""

    output = []
    for chapter in nearby_chapters:
        location = f" — {chapter['location']}" if chapter.get('location') else ""
        output.append(
            f"- **{chapter['name']}**{location} (~{chapter['distance_km']} km) - {chapter['url']}"
        )

    return '\n'.join(output)

def set_github_output(name, value):
    """
    Définir une variable de sortie GitHub Actions.

    Utiliser le format heredoc nécessaire dans $GITHUB_OUTPUT pour les valeurs
    multilignes. L'ancien encodage « %0A » n'est PAS décodé depuis ce fichier et
    apparaîtrait tel quel dans le commentaire publié.
    """
    github_output = os.getenv('GITHUB_OUTPUT')
    if github_output:
        # Délimiteur ne devant pas apparaître dans la valeur, selon les recommandations Actions.
        delimiter = 'EOF_NEARBY_CHAPTERS'
        with open(github_output, 'a') as f:
            f.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")
    else:
        print(f"{name}={value}")

def report(status, nearby_chapters=''):
    """
    Émettre les deux sorties GitHub Actions utilisées par le workflow :
    `status` (found | none | error) et la liste Markdown `nearby_chapters`.
    """
    set_github_output('status', status)
    set_github_output('nearby_chapters', nearby_chapters)

def main():
    """
    Fonction principale de vérification des chapitres voisins.
    """
    issue_body = os.getenv('ISSUE_BODY', '')
    issue_title = os.getenv('ISSUE_TITLE', '')

    print(f"Titre de l'issue : {issue_title}", file=sys.stderr)

    # Extraire le lieu de l'issue. Un corps non analysable n'est pas une erreur d'API et
    # /recheck n'y changera rien : renvoyer « none » (sans commentaire), pas « error ».
    requested_location = extract_location_from_issue(issue_body)

    if not requested_location:
        print("Impossible d'extraire le lieu du corps de l'issue", file=sys.stderr)
        report('none')
        return

    print(f"Lieu demandé : {requested_location}", file=sys.stderr)

    # Géocoder le lieu demandé afin de transmettre ses coordonnées à l'API.
    requested_coords = get_coordinates(requested_location)
    if not requested_coords:
        print(f"Impossible de géocoder le lieu demandé : {requested_location}", file=sys.stderr)
        report('none')
        return

    print(f"Coordonnées du lieu demandé : {requested_coords}", file=sys.stderr)

    # Laisser l'API effectuer la recherche par distance. None indique un échec après plusieurs essais ;
    # renvoyer « error » pour que le workflow propose /recheck.
    chapters = fetch_nearby_chapters(*requested_coords)
    if chapters is None:
        report('error')
        return

    nearby_chapters = annotate_distance(requested_coords, chapters)
    nearby_chapters.sort(key=lambda c: c['distance_km'])

    if nearby_chapters:
        print(f"{len(nearby_chapters)} chapitres voisins trouvés", file=sys.stderr)
        report('found', format_output(nearby_chapters))
    else:
        print("Aucun chapitre voisin trouvé", file=sys.stderr)
        report('none')

if __name__ == '__main__':
    main()
