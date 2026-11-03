# Source this file; no installation or automatic file operations.
if [[ -n "${_SKILLMAN_ACTIVE_BIN:-}" ]]; then
    path=("${(@)path:#${_SKILLMAN_ACTIVE_BIN}}")
fi
typeset -g _SKILLMAN_ROOT="${${(%):-%N}:A:h:h}"
typeset -g _SKILLMAN_ACTIVE_BIN=""
_skillman_chpwd() {
    local entry
    local -a kept
    if [[ -n "$_SKILLMAN_ACTIVE_BIN" ]]; then
        for entry in "${path[@]}"; do
            [[ "$entry" == "$_SKILLMAN_ACTIVE_BIN" ]] || kept+=("$entry")
        done
        path=("${kept[@]}")
        _SKILLMAN_ACTIVE_BIN=""
    fi
    if [[ "$PWD" == "$_SKILLMAN_ROOT" || "$PWD" == "$_SKILLMAN_ROOT/"* ]]; then
        if (( ${path[(Ie)$_SKILLMAN_ROOT/bin]} == 0 )); then
            path=("$_SKILLMAN_ROOT/bin" "${path[@]}")
            _SKILLMAN_ACTIVE_BIN="$_SKILLMAN_ROOT/bin"
        fi
    fi
    rehash
}
autoload -Uz add-zsh-hook
add-zsh-hook -d chpwd _skillman_chpwd 2>/dev/null
add-zsh-hook chpwd _skillman_chpwd
_skillman_chpwd
