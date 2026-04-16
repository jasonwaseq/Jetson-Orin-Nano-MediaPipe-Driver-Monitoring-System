import ipinfo

ACCESS_TOKEN = "24da927605c295"
IP_ADDRESS = "169.233.197.51"

handler = ipinfo.getHandler(ACCESS_TOKEN)
details = handler.getDetails(IP_ADDRESS)

class Location:
    def __init__(self):
        self.location = details.loc

    def get_location(self):
        return self.location

curr = Location()
curr_loc = curr.get_location()

print(curr_loc)
