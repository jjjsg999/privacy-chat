"""Places that are public knowledge and not identifying on their own.

Used when the setting `place_names` is "common_public": a LOCATION finding whose text is one of
these is left readable so the model can answer geography questions. Everything else (small towns,
neighbourhoods, buildings, street addresses) is still masked. Countries and broad regions pass;
cities pass only if they are on the major-city list below. Edit the lists to taste.
"""
import re

COUNTRIES = """
Afghanistan, Albania, Algeria, Andorra, Angola, Antigua and Barbuda, Argentina, Armenia, Australia, Austria,
Azerbaijan, Bahamas, Bahrain, Bangladesh, Barbados, Belarus, Belgium, Belize, Benin, Bhutan, Bolivia,
Bosnia and Herzegovina, Botswana, Brazil, Brunei, Bulgaria, Burkina Faso, Burundi, Cabo Verde, Cambodia,
Cameroon, Canada, Central African Republic, Chad, Chile, China, Colombia, Comoros, Congo, Costa Rica,
Croatia, Cuba, Cyprus, Czechia, Czech Republic, Denmark, Djibouti, Dominica, Dominican Republic, Ecuador,
Egypt, El Salvador, Equatorial Guinea, Eritrea, Estonia, Eswatini, Ethiopia, Fiji, Finland, France, Gabon,
Gambia, Georgia, Germany, Ghana, Greece, Grenada, Guatemala, Guinea, Guinea-Bissau, Guyana, Haiti, Honduras,
Hungary, Iceland, India, Indonesia, Iran, Iraq, Ireland, Israel, Italy, Ivory Coast, Jamaica, Japan, Jordan,
Kazakhstan, Kenya, Kiribati, Kosovo, Kuwait, Kyrgyzstan, Laos, Latvia, Lebanon, Lesotho, Liberia, Libya,
Liechtenstein, Lithuania, Luxembourg, Madagascar, Malawi, Malaysia, Maldives, Mali, Malta, Mauritania,
Mauritius, Mexico, Moldova, Monaco, Mongolia, Montenegro, Morocco, Mozambique, Myanmar, Namibia, Nauru, Nepal,
Netherlands, New Zealand, Nicaragua, Niger, Nigeria, North Korea, North Macedonia, Norway, Oman, Pakistan,
Palau, Palestine, Panama, Papua New Guinea, Paraguay, Peru, Philippines, Poland, Portugal, Qatar, Romania,
Russia, Rwanda, Samoa, San Marino, Saudi Arabia, Senegal, Serbia, Seychelles, Sierra Leone, Singapore,
Slovakia, Slovenia, Solomon Islands, Somalia, South Africa, South Korea, South Sudan, Spain, Sri Lanka, Sudan,
Suriname, Sweden, Switzerland, Syria, Taiwan, Tajikistan, Tanzania, Thailand, Timor-Leste, Togo, Tonga,
Trinidad and Tobago, Tunisia, Turkey, Turkiye, Turkmenistan, Tuvalu, Uganda, Ukraine, United Arab Emirates,
United Kingdom, United States, United States of America, Uruguay, Uzbekistan, Vanuatu, Vatican City,
Venezuela, Vietnam, Viet Nam, Yemen, Zambia, Zimbabwe, Hong Kong, Macau, Macao, Puerto Rico, Greenland,
USA, US, U.S., U.S.A., UK, U.K., UAE, Korea, England, Scotland, Wales, Northern Ireland, Great Britain,
Britain, Holland, Burma
"""

REGIONS = """
Africa, Asia, Europe, North America, South America, Latin America, Central America, Oceania, Antarctica,
Australasia, Middle East, Southeast Asia, South Asia, East Asia, Central Asia, Western Europe, Eastern Europe,
Northern Europe, Southern Europe, Scandinavia, Nordics, Balkans, Caribbean, Pacific, Atlantic, Mediterranean,
EMEA, APAC, Asia Pacific, Sub-Saharan Africa, North Africa, West Africa, East Africa, Gulf, European Union, EU
"""

MAJOR_CITIES = """
Tokyo, Delhi, Shanghai, Sao Paulo, Mexico City, Cairo, Mumbai, Beijing, Dhaka, Osaka, New York, Karachi,
Buenos Aires, Chongqing, Istanbul, Kolkata, Manila, Lagos, Rio de Janeiro, Tianjin, Kinshasa, Guangzhou,
Los Angeles, Moscow, Shenzhen, Lahore, Bangalore, Bengaluru, Paris, Bogota, Jakarta, Chennai, Lima, Bangkok,
Seoul, Nagoya, Hyderabad, London, Tehran, Chicago, Chengdu, Nanjing, Wuhan, Ho Chi Minh City, Saigon, Hanoi,
Luanda, Ahmedabad, Kuala Lumpur, Xi'an, Hong Kong, Dongguan, Hangzhou, Foshan, Shenyang, Riyadh, Baghdad,
Santiago, Surat, Madrid, Suzhou, Pune, Harbin, Houston, Dallas, Toronto, Dar es Salaam, Miami, Belo Horizonte,
Singapore, Philadelphia, Atlanta, Fukuoka, Khartoum, Barcelona, Johannesburg, Saint Petersburg, St Petersburg,
Qingdao, Dalian, Washington, Washington DC, Washington D.C., Yangon, Alexandria, Jinan, Guadalajara, Sydney,
Melbourne, Brisbane, Perth, Auckland, Wellington, Berlin, Munich, Hamburg, Frankfurt, Cologne, Stuttgart,
Dusseldorf, Rome, Milan, Naples, Turin, Vienna, Zurich, Geneva, Basel, Amsterdam, Rotterdam, The Hague, Brussels,
Antwerp, Lisbon, Porto, Dublin, Edinburgh, Glasgow, Manchester, Birmingham, Liverpool, Leeds, Bristol, Oxford,
Cambridge, Copenhagen, Stockholm, Gothenburg, Oslo, Helsinki, Warsaw, Krakow, Prague, Budapest, Bucharest,
Sofia, Athens, Belgrade, Zagreb, Kyiv, Kiev, Minsk, Riga, Tallinn, Vilnius, Reykjavik, Dubai, Abu Dhabi, Doha,
Kuwait City, Manama, Muscat, Jeddah, Mecca, Medina, Amman, Beirut, Damascus, Jerusalem, Tel Aviv, Ankara,
Izmir, Tbilisi, Baku, Yerevan, Tashkent, Almaty, Kabul, Islamabad, Colombo, Kathmandu, Taipei, Kaohsiung,
Taichung, Tainan, Macau, Busan, Incheon, Kyoto, Yokohama, Kobe, Sapporo, Osaka, Shanghai, Phnom Penh,
Vientiane, Penang, Johor Bahru, Cebu, Nairobi, Addis Ababa, Accra, Abuja, Dakar, Casablanca, Marrakesh, Tunis,
Algiers, Tripoli, Cape Town, Durban, Pretoria, Johannesburg, Kampala, Kigali, Lusaka, Harare, Maputo,
San Francisco, San Jose, San Diego, Seattle, Portland, Denver, Phoenix, Las Vegas, Boston, Detroit,
Minneapolis, Austin, San Antonio, Nashville, Charlotte, Orlando, Baltimore, Pittsburgh, Cleveland,
Salt Lake City, Honolulu, Anchorage, Montreal, Vancouver, Calgary, Ottawa, Edmonton, Quebec City, Havana,
San Juan, Panama City, San Jose, Bogota, Medellin, Caracas, Quito, La Paz, Montevideo, Asuncion, Brasilia,
Salvador, Fortaleza, Recife, Curitiba, Porto Alegre
"""


def _normalise(text):
    text = re.sub(r'\s+', ' ', text.strip()).casefold()
    return text[4:] if text.startswith('the ') else text


def _load(block):
    return frozenset(_normalise(name) for name in block.replace('\n', ' ').split(',') if name.strip())


PUBLIC_COUNTRIES = _load(COUNTRIES)
PUBLIC_REGIONS = _load(REGIONS)
PUBLIC_CITIES = _load(MAJOR_CITIES)
PUBLIC_PLACES = PUBLIC_COUNTRIES | PUBLIC_REGIONS | PUBLIC_CITIES


def is_public_place(text):
    """True if `text` is exactly a country, broad region or major city (case-insensitive)."""
    return _normalise(text) in PUBLIC_PLACES
