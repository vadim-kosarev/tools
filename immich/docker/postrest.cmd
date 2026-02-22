call docker run -d --rm --name postgrest -p 3131:3000 -e PGRST_DB_URI="postgres://postgres:postgres@192.168.1.43:5432/immich" -e PGRST_DB_ANON_ROLE="postgres" -e PGRST_DB_SCHEMAS="public"  postgrest/postgrest
call docker run -d --rm --name postrest-swagger-ui -p 8081:8080 -e SWAGGER_JSON_URL=http://localhost:3131/ swaggerapi/swagger-ui
