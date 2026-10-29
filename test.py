print("hola")
while True:
    user_input = input("Enter something (type 'exit' to quit): ")
    if user_input.lower() == 'exit':
        print("Goodbye!")
        break
    else:
        print(f"You entered: {user_input}")