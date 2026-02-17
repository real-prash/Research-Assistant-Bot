import os
import uuid
import markdown
from flask import Flask, render_template, request, session
from dotenv import load_dotenv, find_dotenv

# Import your graph and config
from src.helper import graph
from src.config import llm_planner, llm_worker

# Load Env
load_dotenv(find_dotenv())

app = Flask(__name__)
app.secret_key = os.urandom(24) # Required for session management

@app.route("/")
def index():
    # Keep existing logic...
    session.clear()
    session['thread_id'] = str(uuid.uuid4())
    session['stage'] = 'waiting_for_topic'
    return render_template('chat.html')

@app.route("/how-it-works")
def how_it_works():
    return render_template('how_it_works.html')

@app.route("/get", methods=["POST"])
def chat():
    user_input = request.form["msg"]
    thread_id = session.get('thread_id')
    current_stage = session.get('stage')
    
    config = {"configurable": {"thread_id": thread_id}}
    response_text = ""

    try:
        # --- STAGE 1: User provides TOPIC ---
        if current_stage == 'waiting_for_topic':
            # Run graph to generate analysts
            initial_state = {"topic": user_input, "max_analysts": 3}
            
            # Run until the first interruption (human_feedback node)
            events = list(graph.stream(initial_state, config, stream_mode="values"))
            
            # Extract the latest state with analysts
            final_event = events[-1]
            analysts = final_event.get('analysts', [])
            
            # Format the Analyst List for the Chat UI
            response_text += "<strong>🕵️ I have generated the following analysts for your topic:</strong><br><br>"
            for i, agent in enumerate(analysts, 1):
                response_text += f"<b>{i}. {agent.name}</b> ({agent.role})<br><i>{agent.affiliation}</i><br><br>"
            
            response_text += "⚠️ <b>Feedback required:</b> Type 'Approve' to proceed, or describe any changes you want to make to these personas."
            
            # Update State
            session['stage'] = 'waiting_for_feedback'

        # --- STAGE 2: User provides FEEDBACK (HITL) ---
        elif current_stage == 'waiting_for_feedback':
            feedback = user_input.strip()
            
            # === OPTION A: APPROVAL ===
            if feedback.lower() in ['approve', 'yes', 'ok', 'go', 'proceed', 'no']:
                # User is happy. Clear feedback so the graph proceeds to research.
                graph.update_state(config, {"human_analyst_feedback": None}, as_node="human_feedback")
                
                status_msg = "🚀 <b>Starting Research...</b><br><i>This may take 2-3 minutes. Please stay on this page.</i>"
                final_report = ""

                # Run the graph. It will proceed to interviews -> report -> end.
                for event in graph.stream(None, config, stream_mode="values"):
                    if 'final_report' in event:
                        final_report = event['final_report']

                # If we got a final report, we are done
                if final_report:
                    html_report = markdown.markdown(final_report)
                    response_text = status_msg + "<hr>" + html_report
                    
                    # Reset for next topic
                    session['stage'] = 'waiting_for_topic'
                    session['thread_id'] = str(uuid.uuid4())
            
            # === OPTION B: REQUEST CHANGES ===
            else:
                # User wants changes. Update state with their feedback.
                graph.update_state(config, {"human_analyst_feedback": feedback}, as_node="human_feedback")
                
                # Run the graph. 
                # Logic: initiate_all_interviews will see the feedback -> route to create_analysts -> interrupt again.
                events = list(graph.stream(None, config, stream_mode="values"))
                
                # The last event contains the NEW analysts generated using your feedback
                final_event = events[-1]
                
                if 'analysts' in final_event:
                    analysts = final_event['analysts']
                    response_text += f"<strong>🔄 Updated Analysts (based on: '{feedback}'):</strong><br><br>"
                    for i, agent in enumerate(analysts, 1):
                        response_text += f"<b>{i}. {agent.name}</b> ({agent.role})<br><i>{agent.affiliation}</i><br><br>"
                    
                    response_text += "⚠️ <b>Feedback required:</b> Type 'Approve' to proceed, or describe further changes."
                    
                    # STAY in the feedback stage
                    session['stage'] = 'waiting_for_feedback'

    except Exception as e:
        print(f"Error: {e}")
        response_text = "❌ An error occurred. Please refresh the page and try again."

    return response_text

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=8080, debug=True)