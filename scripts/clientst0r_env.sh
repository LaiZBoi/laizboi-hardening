# Shared helpers for Client St0r shell scripts.

clientst0r_load_env() {
    local env_file="${1:-}"
    [ -n "$env_file" ] && [ -f "$env_file" ] || return 0

    while IFS= read -r line || [ -n "$line" ]; do
        line="${line%$'\r'}"
        case "$line" in
            ''|\#*) continue ;;
            *=*)
                local key="${line%%=*}"
                local val="${line#*=}"
                key="${key#"${key%%[![:space:]]*}"}"
                key="${key%"${key##*[![:space:]]}"}"
                val="${val#"${val%%[![:space:]]*}"}"
                val="${val%"${val##*[![:space:]]}"}"
                val="${val#\"}"; val="${val%\"}"
                val="${val#\'}"; val="${val%\'}"
                export "$key=$val"
                ;;
        esac
    done < "$env_file"
}

clientst0r_auto_update_enabled() {
    case "${AUTO_UPDATE_ENABLED,,}" in
        true|1|yes) return 0 ;;
        *) return 1 ;;
    esac
}

clientst0r_load_deployment_env() {
    local project_dir="${1:-}"
    if [ -n "$project_dir" ] && [ -f "$project_dir/.env" ]; then
        clientst0r_load_env "$project_dir/.env"
        return 0
    fi
    if [ -f /etc/clientst0r/.env ]; then
        clientst0r_load_env /etc/clientst0r/.env
    fi
}
