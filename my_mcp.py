from mcp.server.fastmcp import FastMCP
import random
mcp = FastMCP('my-server')

secret_word_list100 = [
    "Madrid", "Barcelona", "Valencia", "Sevilla", "Zaragoza", "Málaga", "Murcia", 
    "Palma de Mallorca", "Las Palmas de Gran Canaria", "Bilbao", "Alicante", 
    "Córdoba", "Valladolid", "Vigo", "Gijón", "Hospitalet de Llobregat", 
    "Vitoria", "La Coruña", "Elche", "Granada", "Tarrasa", "Badalona", "Oviedo", 
    "Cartagena", "Sabadell", "Jerez de la Frontera", "Móstoles", 
    "Santa Cruz de Tenerife", "Pamplona", "Almería", "Alcalá de Henares", 
    "Fuenlabrada", "Leganés", "San Sebastián", "Getafe", "Burgos", "Albacete", 
    "Castellón de la Plana", "Santander", "Alcorcón", "San Cristóbal de La Laguna", 
    "Logroño", "Badajoz", "Huelva", "Salamanca", "Marbella", "Lérida", 
    "Dos Hermanas", "Tarragona", "Torrejón de Ardoz", "Parla", "Mataró", 
    "Algeciras", "León", "Santa Coloma de Gramanet", "Alcobendas", "Cádiz", 
    "Jaén", "Orense", "Reus", "Telde", "Gerona", "Baracaldo", "Lugo", 
    "Santiago de Compostela", "Cáceres", "Las Rozas de Madrid", "San Fernando", 
    "Roquetas de Mar", "Lorca", "San Cugat del Vallés", "Arona", 
    "San Sebastián de los Reyes", "Cornellá de Llobregat", "Melilla", 
    "Pozuelo de Alarcón", "Coslada", "Ceuta", "Torrevieja", "Talavera de la Reina", 
    "Guadalajara", "Toledo", "Pontevedra", "Palencia", "Mijas", 
    "Chiclana de la Frontera", "Torrente", "San Baudilio de Llobregat", 
    "Vélez-Málaga", "Gandía", "Fuengirola", "Manresa", "Alcalá de Guadaíra", 
    "Rubí", "Valdemoro", "Ferrol", "Majadahonda", "Benidorm", "Molina de Segura", 
    "Santa Lucía de Tirajana"
]  # List of 100 secret words

@mcp.tool()
def secret() -> str:
    '''This is a secret tool that returns a secret word.'''
    random_number = random.randint(0, 99)
    return f"The secret word is {secret_word_list100[random_number]}."



if __name__ == "__main__":
    mcp.run()

