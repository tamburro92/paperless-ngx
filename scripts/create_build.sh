#!/bin/bash

# Verifica se è stata fornita la versione come parametro
if [ "$#" -ne 1 ]; then
	echo "Uso: $0 <versione>"
	exit 1
fi

VERSION="$1"

BASE_NAME="paperless-ngx-new"
PACKAGE_NAME="${BASE_NAME}_${VERSION}"

# Imposta la directory di distribuzione
DIST_DIR="dist"

# Crea la cartella di distribuzione se non esiste
if [ ! -d "$DIST_DIR" ]; then
	echo "Creando la cartella di distribuzione: $DIST_DIR"
	mkdir -p $DIST_DIR
else
	echo "La cartella di distribuzione già esiste: $DIST_DIR"
fi

# Naviga nella directory della tua app Django
cd src-ui

#npm install
#ng build --configuration production

cd ../src

pipenv run python manage.py collectstatic --clear --noinput

cd ..

pipenv requirements > requirements.txt



FILE_LIST=("src" "static" "docs" "scripts" "Dockerfile" "Pipfile" "docker" "License" "README" "Pipfile.lock" "gunicorn.conf.py" "gunicorn.conf.py" "requirements.txt")  # Sostituisci con i tuoi file e directory

# Crea il pacchetto tar.xz con i file specificati
echo "Creando il pacchetto di distribuzione..."
tar -cvJf "$DIST_DIR/${PACKAGE_NAME}.tar.xz" "${FILE_LIST[@]}"

echo "Pacchetto creato in $DIST_DIR/${PACKAGE_NAME}.tar.xz"
