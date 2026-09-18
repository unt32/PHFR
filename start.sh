#!/usr/bin/env bash

SESSION="phfr_dev"
tmux kill-session -t '$SESSION' 2>/dev/null

# trap "tmux kill-session -t '$SESSION' 2>/dev/null" EXIT SIGINT SIGTERM

tmux new-session -d -s $SESSION -n 'dev' '.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000; bash'
tmux split-window -v -t $SESSION:0 'cd web && npm run dev:lan; bash'

foot bash -c "tmux attach-session -t '$SESSION'"
