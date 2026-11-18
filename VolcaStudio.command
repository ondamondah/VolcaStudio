#!/bin/zsh

cd -- "${0:A:h}" || exit 1
export PYTHONDONTWRITEBYTECODE=1

fail() {
  print -u2 -r -- "$1"
  if [[ -t 0 ]]; then
    read '?Нажмите Enter, чтобы закрыть окно…'
  fi
  exit 1
}

if [[ ! -x .venv/bin/python ]]; then
  python_bin=$(command -v python3.11)
  if [[ -z "$python_bin" ]]; then
    python_bin=/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11
  fi
  [[ -x "$python_bin" ]] || fail 'Для запуска нужен Python 3.11. См. README.md.'
  "$python_bin" -m venv .venv || fail 'Не удалось подготовить окружение VolcaStudio.'
fi

if ! /usr/bin/cmp -s requirements.txt .venv/volcastudio-requirements.txt; then
  .venv/bin/python -m pip install -r requirements.txt || fail 'Не удалось установить зависимости. См. README.md.'
  /bin/cp requirements.txt .venv/volcastudio-requirements.txt || fail 'Не удалось сохранить состояние установки.'
fi

.venv/bin/python -m app.main || fail 'VolcaStudio завершилась с ошибкой. Подробности выше.'
