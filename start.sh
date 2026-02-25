#!/bin/bash
# Start both backend and frontend servers for development

echo "Starting Soren..."

# Start backend
echo "Starting backend on http://localhost:8000"
cd backend
PYTHONPATH=. .venv/bin/uvicorn app.main:app --port 8000 --reload &
BACKEND_PID=$!
cd ..

# Start frontend
echo "Starting frontend on http://localhost:3000"
cd frontend
npx next dev --port 3000 &
FRONTEND_PID=$!
cd ..

echo ""
echo "Soren is running:"
echo "  Backend:  http://localhost:8000"
echo "  Frontend: http://localhost:3000"
echo "  API Docs: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop both servers"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
