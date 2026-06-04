import asyncio
from ...schemas import ConversationSchema

async def fake_ai(conversation: ConversationSchema, model: str, max_inferences = 5):
    """
    Simulador puro de openai_agent para pruebas de streaming.
    Entrega una lista de tokens de forma progresiva durando exactamente 12 segundos.
    """
    print(f"[FAKE_AI] Iniciando simulación de streaming de 12s con modelo: {model}")
    
    texto_base = (
        "Iniciando secuencia de verificación de doce segundos. "
        "Este es un flujo controlado de tokens transmitidos a través de la cola de memoria RAM. "
        "Ninguna API externa está siendo consultada en este momento. "
        "Estamos midiendo la estabilidad del socket y la capacidad de renderizado progresivo de la interfaz gráfica. "
        "El búfer se vacía de manera uniforme sin provocar bloqueos en el bucle de eventos de FastAPI. "
        "La persistencia en la base de datos se ejecutará inmediatamente después de recibir el último fragmento. "
        "Finalizando simulación de streaming con éxito."
    )
    
    # Fragmentamos el texto en palabras con su espacio correspondiente (tokens emulados)
    tokens = [word + " " for word in texto_base.split(" ")]
    total_tokens = len(tokens)
    
    # Duración total objetiva: 12 segundos
    # Calculamos el retraso exacto por token: (12 segundos / total de tokens)
    delay_per_token = 12.0 / total_tokens
    
    try:
        for i, token in enumerate(tokens):
            yield token
            await asyncio.sleep(delay_per_token)
            
    except Exception as e:
        print(f"[FAKE_AI ERROR] {str(e)}")
        yield 'ERROR_TOKEN'
    return