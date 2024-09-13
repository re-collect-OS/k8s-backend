FROM python:3.11.6

RUN apt-get update

RUN pip install alembic==1.12.1 psycopg2-binary==2.9.9

COPY alembic.ini /opt/alembic/alembic.ini
COPY migrations/pgsql /opt/alembic/migrations/pgsql

WORKDIR /opt/alembic

CMD ["alembic", "upgrade", "head"]
