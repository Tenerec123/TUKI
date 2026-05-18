# T.U.K.I. - TODO Project

T.U.K.I. (T_ENEREC's U_NIFIED K_NOWLEDGE I_NTEGRATOR) is a comprehensive TODO management system built with FastAPI and SQLAlchemy.

## Features

- ✅ **Projects Management** - Create hierarchical projects with sub-projects
- ✅ **Tasks** - Manage tasks with deadlines and priority levels
- ✅ **Routines** - Schedule recurring tasks with frequency settings
- ✅ **Routine Heatmap** - Visual representation of routine completion patterns
- ✅ **AI Chat** - Integrated AI assistant powered by Google Gemini
- ✅ **Conversations** - Store and manage chat histories

## Tech Stack

- **Backend**: FastAPI, SQLAlchemy, Pydantic
- **Database**: SQLite
- **AI**: Google Gemini API, OpenRouter API
- **Frontend**: Vanilla JavaScript, HTML5, CSS3

## Installation

### Prerequisites
- Python 3.10+
- pip

### Setup

1. **Clone the repository**
```bash
git clone <repository-url>
cd TODO_Project
```

2. **Create virtual environment**
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. **Install dependencies**
```bash
pip install -r requirements.txt
```

4. **Configure environment variables**
```bash
cp .env.example .env
# Edit .env with your API keys
```

5. **Run the application**
```bash
python -m fastapi dev backend/main.py --host 0.0.0.0
```

The application will be available at `http://localhost:8000`

## API Documentation

Once running, visit `http://localhost:8000/docs` for interactive API documentation (Swagger UI)

## Project Structure

```
backend/
  ├── models.py                # Database models
  ├── database.py              # Database configuration
  ├── schemas.py               # Pydantic schemas
  ├── main.py                  # FastAPI app setup
  ├── alembic/                 # Database migrations
  └── routers/
      ├── tasks.py             # Task endpoints
      ├── tasks_logic.py        # Task business logic
      ├── projects.py          # Project endpoints
      ├── projects_logic.py     # Project business logic
      ├── routines.py          # Routine endpoints
      ├── routines_logic.py     # Routine business logic
      ├── ai.py                # AI chat endpoints
      ├── conversations.py      # Conversation history endpoints
      └── tools.py             # Utility functions for AI tools

frontend/
  ├── todo.html                # Task management UI
  ├── chat.html                # AI chat interface
  ├── kale.html                # Routine heatmap visualization
  ├── todo_script.js
  ├── chat_script.js
  ├── kale_script.js
  └── style.css
```

## Available Routes

### Tasks
- `GET /api/tasks/` - Get all tasks
- `GET /api/tasks/{id}` - Get specific task
- `POST /api/tasks/` - Create task
- `PATCH /api/tasks/{id}` - Update task
- `DELETE /api/tasks/{id}` - Delete task

### Projects
- `GET /api/projects/` - Get all projects
- `GET /api/projects/{id}` - Get specific project
- `POST /api/projects/` - Create project
- `PATCH /api/projects/{id}` - Update project
- `DELETE /api/projects/{id}` - Delete project

### Routines
- `GET /api/routines/` - Get all routines
- `GET /api/routines/{id}` - Get specific routine
- `POST /api/routines/` - Create routine
- `PATCH /api/routines/{id}` - Update routine
- `DELETE /api/routines/{id}` - Delete routine

### AI & Conversations
- `POST /api/ai/prompt` - Send message to AI
- `GET /api/conversations/` - Get all conversations
- `POST /api/conversations/` - Create conversation

## Configuration

### Environment Variables

See `.env.example` for all available options:

- `GOOGLE_GENAI_API_KEY` - Your Google Gemini API key
- `OPENROUTER_API_KEY` - Your OpenRouter API key
- `DATABASE_URL` - Database connection string
- `ENVIRONMENT` - development, staging, or production

## Development

### Running locally with auto-reload
```bash
python -m fastapi dev backend/main.py --host 0.0.0.0
```

### Database Migrations (Alembic)
```bash
cd backend
alembic upgrade head  # Apply migrations
alembic revision --autogenerate -m "Description"  # Create migration
```
