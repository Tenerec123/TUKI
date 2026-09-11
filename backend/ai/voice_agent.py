from .stt import stt_conversion_logic
from .agent import openai_agent
from .config import get_model_config, AUDIO_SYSTEM_PROMPT
from .tools.discovery import ALL_TOOL_SCHEMAS
from .tts import text_to_speech_wav
import wave
import io

async def voice_agent_logic(stream):
    
    audio_buffer = io.BytesIO()

    with wave.open(audio_buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        async for chunk in stream:
            wav_file.writeframes(chunk)

    audio_buffer.seek(0)
    transcription = await stt_conversion_logic(audio_buffer)
    print(f"[STT] Transcription: {transcription}")
    messages = [{'role':'developer','content':AUDIO_SYSTEM_PROMPT},{'role':'user','content':transcription}]
    async for _ in openai_agent(
        messages=messages,
        model=get_model_config()['orchestrator'],
        max_rounds=10,
        tool_schemas=ALL_TOOL_SCHEMAS,
    ):
        pass  # iteramos para que el agente realmente ejecute
    agent_response = messages[-1]['content']
    print(f"[TTS] Responding: {agent_response}")
    return await text_to_speech_wav(agent_response)
