def add_task():
    task=input("enter your task: ")
    with open("task.txt","a") as f:
        f.write(task +"\n")
def view_task():
   try:
        with open("task.txt","r") as f:
            tasks=f.readlines()
            for x in tasks:
                print(x.strip())
except FileNotFoundError:
    print("There Is No Tasks Are Avaliable")
while True:
    print("-----See Your Choice-------")
    print("1.ADD TASKS")
    print("2.VIEW TASKS")
    print("3.EXIT")

    try:
        n= int(input("Enter your choice From The Above: "))
        if n==1:
            add_task()
        elif n==2:
            view_task()
        elif n==3:
            break
        else:
            print("Enter The Avaliable Choices Only")
    except ValueError:
        print("Enter Only Numbers")
