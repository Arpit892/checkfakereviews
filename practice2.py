x= "912" #here the num is in string

x= int(x) #converted into integer using int(x)
print(x)

y= float(x) #here it is converted in to float and we also changed the name of the variable
print(y)

x= complex(x)  #printed complex number
print(x)

print(complex(2,4))

y= -7.5
print(abs(y))  #here it will print positive version

import math
z= 10
print(math.exp(z))

print(math.e)   #pre stored constant
print(math.pi)  #pre stored pi value

print(math.sqrt(5))

#creating list

num= [2,5,8,6,4]
print(num)

letter= ['a','b','c','d']
print(letter)

stg= ["adad", "vadada"]
print(stg)

mix= [2,4,6,'a','b',"c",'aa']
print(mix)

matrix= [[1,2],['c','d']]
print(matrix)

print(mix)
print(mix[3])
print(mix[-4])  #to extract from back
print(mix[:3])
print(mix[3:])
print(mix[3:4])
print(mix[::2])    #to extract every second element of list
print(mix[::-1])   #to reverse the list


#####operations on list####

print([0]*100)     #to print an element of the list 100 times


print(letter)
print(stg)

conc= letter+stg   #to add up two lists together and make them one
print(conc)

var=list("hey there")    #to make any string a list
print(var)

print(num)

one,*other= num   #to exclude one element from the list
print(one)
print(other)


####methods in list####

print(num)
num.append(7)   #to add an element into list
print(num)

num.extend(stg)    #to add an entire list into another
print(num)

num.insert(5,"suu")   #to insert an element in middle of the list here i have inserted "suu" on index "5"
print(num)

num.remove("adad")   #to remove an element
print(num)

var1= ["t","g","e","i","r"]
var1.sort()
print(var1)

######built in functions in list###

var2= [5,8,9,6,4,]        #to know the length of the list
print(len(var2))

print(min(var2))        #to know the minimum value
print(max(var2))        #to know teh maximum value
print(sum(var2))       #to know the sum
print(sum(var2)/len(var2))   #to know the average of list
